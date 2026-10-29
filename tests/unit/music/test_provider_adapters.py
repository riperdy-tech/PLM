"""Unit tests for music factory provider protocol and adapters (§4.4, §111)."""

import pytest

from services.music_factory.domain.enums import AttemptStatus
from services.music_factory.domain.models import GenerationRequest
from services.music_factory.providers.adapters import (
    ElevenLabsAdapter,
    MockAdapter,
    SunoAdapter,
    get_provider_adapter,
)
from services.music_factory.providers.protocol import MusicProviderProtocol


def test_adapters_satisfy_protocol() -> None:
    mock = MockAdapter()
    suno = SunoAdapter()
    eleven = ElevenLabsAdapter()

    assert isinstance(mock, MusicProviderProtocol)
    assert isinstance(suno, MusicProviderProtocol)
    assert isinstance(eleven, MusicProviderProtocol)


def test_factory_creation() -> None:
    adapter1 = get_provider_adapter("mock")
    assert adapter1.provider_id == "mock"
    assert adapter1.model_id == "mock-v1"

    adapter2 = get_provider_adapter("suno", model_id="chirp-v3")
    assert adapter2.provider_id == "suno"
    assert adapter2.model_id == "chirp-v3"

    adapter3 = get_provider_adapter("elevenlabs")
    assert adapter3.provider_id == "elevenlabs"

    with pytest.raises(ValueError, match="Unknown music provider"):
        get_provider_adapter("unsupported_provider")


def test_mock_adapter_generation() -> None:
    adapter = MockAdapter()
    assert adapter.check_health() is True
    quota = adapter.get_quota_status()
    assert quota["quota_exhausted"] is False

    req = GenerationRequest(
        experiment_id="exp_test_01",
        track_spec_id="spec_test_01",
        prompt="ambient synthwave 120 bpm",
        seed=42,
        duration_seconds=5,
    )
    res = adapter.generate(req)
    assert res.status == AttemptStatus.SUCCEEDED
    assert res.audio_data is not None
    assert len(res.audio_data) > 0


def test_unconfigured_external_adapters_fail_cleanly() -> None:
    suno = SunoAdapter(api_key="")
    assert suno.check_health() is False
    assert suno.license_terms() is None
    req = GenerationRequest(
        experiment_id="exp_test_01",
        track_spec_id="spec_suno_01",
        prompt="lo-fi beat",
        seed=1,
    )
    res = suno.generate(req)
    assert res.status == AttemptStatus.FAILED
    assert res.provider_job_id == ""
    assert res.confirmed_unsubmitted is True
    assert "missing" in res.error_message.lower() or "not configured" in res.error_message.lower()


@pytest.mark.parametrize(
    ("token", "configured", "status"),
    [("", False, "not_configured"), ("test_token", True, "unknown")],
)
def test_elevenlabs_key_does_not_invent_quota(
    monkeypatch: pytest.MonkeyPatch, token: str, configured: bool, status: str,
) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    provider = ElevenLabsAdapter(api_key=token)
    quota = provider.get_quota_status()
    assert quota["configured"] is configured
    assert quota["credits_remaining"] is None
    assert quota["quota_exhausted"] is None
    assert quota["quota_known"] is False
    assert quota["quota_status"] == status


def test_suno_unconfigured_quota_is_not_confused_with_zero() -> None:
    suno = SunoAdapter(api_key="")
    quota = suno.get_quota_status()
    assert quota["configured"] is False
    assert quota["credits_remaining"] is None
    assert quota["quota_exhausted"] is None
    assert quota["quota_status"] == "not_configured"


def test_suno_api_key_never_asserts_unverified_commercial_license() -> None:
    suno = SunoAdapter(api_key="test_token")
    assert suno.license_terms() is None


@pytest.mark.parametrize("payload", [
    {}, {"total_credits_left": None}, {"total_credits_left": "unknown"},
    {"total_credits_left": -1}, {"total_credits_left": True},
])
def test_suno_quota_missing_or_malformed_is_not_fabricated(
    monkeypatch: pytest.MonkeyPatch, payload: dict,
) -> None:
    suno = SunoAdapter(api_key="test_token")
    monkeypatch.setattr(suno._client, "get_billing_info", lambda: payload)
    quota = suno.get_quota_status()
    assert quota["credits_remaining"] is None
    assert quota["quota_known"] is False
    assert quota["quota_exhausted"] is None
    assert quota["quota_status"] == "unknown"


def test_suno_quota_errors_and_known_values_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suno = SunoAdapter(api_key="test_token")

    def unavailable() -> dict:
        raise RuntimeError("billing endpoint unavailable")

    monkeypatch.setattr(suno._client, "get_billing_info", unavailable)
    quota = suno.get_quota_status()
    assert quota["quota_known"] is False
    assert quota["credits_remaining"] is None
    assert quota["quota_exhausted"] is None
    assert quota["quota_status"] == "unknown"

    monkeypatch.setattr(
        suno._client, "get_billing_info", lambda: {"total_credits_left": "12"},
    )
    quota = suno.get_quota_status()
    assert quota["quota_known"] is True
    assert quota["credits_remaining"] == 12
    assert quota["quota_exhausted"] is False
    assert quota["quota_status"] == "observed"


@pytest.mark.parametrize(("vocal_mode", "reported_model"), [
    ("instrumental", "chirp-v3-5"),
    ("vocal", "chirp-v3-5"),
    ("vocal", ""),
])
def test_suno_adapter_generation_mocked(
    monkeypatch: pytest.MonkeyPatch, vocal_mode: str, reported_model: str,
) -> None:
    from services.music_factory.providers.suno_client import SunoClip

    suno = SunoAdapter(api_key="test_token")

    def mock_submit(*args: object, **kwargs: object) -> tuple[str, ...]:
        return ("clip_test_123",)

    def mock_poll(*args: object, **kwargs: object) -> SunoClip:
        return SunoClip(
            clip_id="clip_test_123",
            status="complete",
            title="PLM_Test",
            audio_url="https://fake.suno.ai/track.mp3",
            video_url=None,
            image_url=None,
            duration_seconds=185.0,
            model_name=reported_model,
            tags="lofi chill",
        )

    def mock_wav(*args: object, **kwargs: object) -> tuple[bytes, float]:
        return (b"RIFF mock wav data", 185.0)

    monkeypatch.setattr(suno._client, "submit_generation", mock_submit)
    monkeypatch.setattr(suno._client, "poll_clip", mock_poll)
    monkeypatch.setattr(suno._client, "download_clip_wav", mock_wav)

    req = GenerationRequest(
        experiment_id="exp_test_01",
        track_spec_id="spec_suno_01",
        prompt="lo-fi chill study beats",
        vocal_mode=vocal_mode,
        seed=1,
    )
    res = suno.generate(req)
    assert res.status == AttemptStatus.SUCCEEDED
    assert res.provider_job_id == "clip_test_123"
    assert res.actual_duration_seconds == 185.0
    assert res.audio_data == b"RIFF mock wav data"
    assert res.mime_type == "audio/wav"
    assert res.cost_credits is None  # Suno supplies no usage receipt; not 10 or zero.
    assert res.credit_receipt_ref is None
    assert res.model_version == (reported_model or None)
    # The prompt is an instruction, not a provider-generated transcript.
    # Missing actual vocal lyrics must remain UNKNOWN to the P5 detector.
    assert res.lyrics_text is None


@pytest.mark.parametrize(("failure_stage", "expected_clip_id"), [
    ("submit_error", ""),
    ("missing_receipt", ""),
    ("poll_error", "real-clip-from-provider"),
    ("download_error", "real-clip-from-provider"),
])
def test_suno_error_never_invents_provider_job_identity(
    monkeypatch: pytest.MonkeyPatch, failure_stage: str, expected_clip_id: str,
) -> None:
    """Suno errors do not invent accepted receipts or claim no charge."""
    from services.music_factory.providers.suno_client import SunoClientError

    suno = SunoAdapter(api_key="test_token")

    def raise_provider_error(*args: object, **kwargs: object) -> None:
        raise SunoClientError("provider response unavailable")

    if failure_stage == "submit_error":
        monkeypatch.setattr(suno._client, "submit_generation", raise_provider_error)
    elif failure_stage == "missing_receipt":
        monkeypatch.setattr(suno._client, "submit_generation", lambda **kwargs: ())
    else:
        monkeypatch.setattr(
            suno._client, "submit_generation",
            lambda **kwargs: ("real-clip-from-provider",),
        )
        if failure_stage == "poll_error":
            monkeypatch.setattr(suno._client, "poll_clip", raise_provider_error)
        else:
            monkeypatch.setattr(suno._client, "poll_clip", lambda clip_id: None)
            monkeypatch.setattr(suno._client, "download_clip_wav", raise_provider_error)

    result = suno.generate(GenerationRequest(
        experiment_id="exp_error", track_spec_id="spec_error",
        prompt="instrumental", seed=1,
    ))
    assert result.status is AttemptStatus.FAILED
    assert result.provider_job_id == expected_clip_id
    assert result.confirmed_unsubmitted is False
    assert "suno_err_" not in result.provider_job_id


def test_suno_client_download_clip_wav_prefers_video_demux(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.music_factory.providers.suno_client import SunoClient

    client = SunoClient(token="mock_token")
    video_demux_called = False

    def mock_video(clip_id: str, **kwargs: object) -> tuple[bytes, float]:
        nonlocal video_demux_called
        video_demux_called = True
        return (b"RIFF video audio", 120.0)

    monkeypatch.setattr(client, "extract_audio_via_video", mock_video)
    wav_bytes, dur = client.download_clip_wav("test_clip_id", prefer_video_demux=True)

    assert video_demux_called is True
    assert wav_bytes == b"RIFF video audio"
    assert dur == 120.0

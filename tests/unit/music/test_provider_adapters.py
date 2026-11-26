"""Unit tests for music factory provider protocol and adapters (§4.4, §111)."""

import pytest

from services.music_factory.domain.enums import AttemptStatus
from services.music_factory.domain.models import GenerationRequest
from services.music_factory.providers.adapters import (
    ElevenLabsAdapter,
    MockAdapter,
    get_provider_adapter,
)
from services.music_factory.providers.protocol import MusicProviderProtocol


def test_adapters_satisfy_protocol() -> None:
    mock = MockAdapter()
    eleven = ElevenLabsAdapter()

    assert isinstance(mock, MusicProviderProtocol)
    assert isinstance(eleven, MusicProviderProtocol)


def test_factory_creation() -> None:
    adapter1 = get_provider_adapter("mock")
    assert adapter1.provider_id == "mock"
    assert adapter1.model_id == "mock-v1"

    with pytest.raises(ValueError, match="automated Suno client was removed"):
        get_provider_adapter("suno")

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
    suno = ElevenLabsAdapter(api_key="")
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

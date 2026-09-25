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
    req = GenerationRequest(
        experiment_id="exp_test_01",
        track_spec_id="spec_suno_01",
        prompt="lo-fi beat",
        seed=1,
    )
    res = suno.generate(req)
    assert res.status == AttemptStatus.FAILED
    assert "missing" in res.error_message.lower() or "not configured" in res.error_message.lower()

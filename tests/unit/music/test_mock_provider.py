"""Tests for mock music provider and deterministic synthesis (Task P4.4)."""

from __future__ import annotations

import io
import wave

from services.music_factory.domain.enums import AttemptStatus
from services.music_factory.domain.models import GenerationRequest
from services.music_factory.providers.mock_provider import MockMusicProvider, synthesize_mock_wav


def test_synthesize_mock_wav_produces_valid_wav() -> None:
    data = synthesize_mock_wav(duration_seconds=2.0, sample_rate=44100, channels=2, seed=42)
    assert len(data) > 0
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"

    # Read back through wave standard library
    with wave.open(io.BytesIO(data), "rb") as reader:
        assert reader.getnchannels() == 2
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 44100
        assert reader.getnframes() == 2 * 44100


def test_synthesize_mock_wav_is_deterministic() -> None:
    data1 = synthesize_mock_wav(duration_seconds=1.0, seed=123)
    data2 = synthesize_mock_wav(duration_seconds=1.0, seed=123)
    assert data1 == data2

    data3 = synthesize_mock_wav(duration_seconds=1.0, seed=456)
    assert data1 != data3


def test_mock_provider_success_flow() -> None:
    provider = MockMusicProvider()
    req = GenerationRequest(
        track_spec_id="trk_test_01",
        experiment_id="exp_01",
        prompt="Late night rainy R&B moody beat",
        duration_seconds=5,
        seed=1,
    )
    resp = provider.generate(req)
    assert resp.status_code if hasattr(resp, "status_code") else resp.status == AttemptStatus.SUCCEEDED
    assert resp.audio_data is not None
    assert len(resp.audio_data) > 1000
    assert resp.mime_type == "audio/wav"
    assert resp.cost_credits == 1.0


def test_mock_provider_error_simulation_hooks() -> None:
    provider = MockMusicProvider()

    # Timeout
    resp_to = provider.generate(
        GenerationRequest(
            track_spec_id="trk_to",
            experiment_id="exp_01",
            prompt="[SIMULATE_TIMEOUT] slow track",
        )
    )
    assert resp_to.status == AttemptStatus.FAILED
    assert "timed out" in (resp_to.error_message or "")

    # Quota
    resp_quota = provider.generate(
        GenerationRequest(
            track_spec_id="trk_q",
            experiment_id="exp_01",
            prompt="[SIMULATE_QUOTA_EXCEEDED] expensive track",
        )
    )
    assert resp_quota.status == AttemptStatus.FAILED
    assert "quota exceeded" in (resp_quota.error_message or "")

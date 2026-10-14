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
    assert resp.status == AttemptStatus.SUCCEEDED
    assert resp.audio_data is not None
    assert len(resp.audio_data) > 1000
    assert resp.mime_type == "audio/wav"
    assert resp.cost_credits == 1.0


def test_mock_provider_honours_a_three_minute_request() -> None:
    """ADR-0032: no 30 s clamp, so a mock run exercises P5's real duration gate."""
    provider = MockMusicProvider()
    req = GenerationRequest(
        track_spec_id="trk_long", experiment_id="exp_01", prompt="rainy lofi", duration_seconds=180, seed=7
    )
    resp = provider.generate(req)
    assert resp.status == AttemptStatus.SUCCEEDED
    assert resp.actual_duration_seconds == 180.0
    assert resp.audio_data is not None
    with wave.open(io.BytesIO(resp.audio_data), "rb") as reader:
        assert reader.getframerate() == 44100  # what P5's technical check expects
        assert reader.getnchannels() == 2
        assert reader.getnframes() == 180 * 44100
        frames = reader.readframes(reader.getnframes())
    assert any(frames)  # not silence, which P5 would block
    assert provider.generate(req).audio_data == resp.audio_data  # deterministic


def test_synthesize_mock_wav_handles_fractional_and_sub_second_durations() -> None:
    for duration in (0.25, 1.5, 2.75):
        data = synthesize_mock_wav(duration_seconds=duration, seed=3)
        with wave.open(io.BytesIO(data), "rb") as reader:
            assert reader.getnframes() == round(duration * 44100)


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

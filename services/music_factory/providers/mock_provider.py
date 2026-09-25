"""Deterministic mock AI music generation provider for unit/integration testing (§4.4, §111)."""

from __future__ import annotations

import io
import math
import struct
import wave
from typing import Any

from ..domain.enums import AttemptStatus
from ..domain.models import GenerationRequest, GenerationResponse

__all__ = ["MockMusicProvider", "synthesize_mock_wav"]


def synthesize_mock_wav(
    *,
    duration_seconds: float = 5.0,
    sample_rate: int = 44100,
    channels: int = 2,
    frequency: float = 440.0,
    seed: int = 0,
) -> bytes:
    """Generate deterministic, valid PCM 16-bit stereo/mono WAV audio bytes.

    Uses standard `wave` module so the output is verifiable by any audio reader.
    """
    total_frames = int(duration_seconds * sample_rate)
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)

        # Generate a simple deterministic sine wave modulated by seed
        freq = frequency + (seed % 100)
        frames = bytearray()
        amplitude = 16000  # comfortable non-clipping volume for 16-bit (-32768 to 32767)

        for i in range(total_frames):
            t = float(i) / sample_rate
            sample = int(amplitude * math.sin(2.0 * math.pi * freq * t))
            packed = struct.pack("<h", sample)
            for _ in range(channels):
                frames.extend(packed)

        wav_file.writeframes(bytes(frames))

    return buffer.getvalue()


class MockMusicProvider:
    """Mock music provider implementing deterministic synthesis and error simulation."""

    def __init__(self, *, provider_id: str = "mock", model_id: str = "mock-v1") -> None:
        self.provider_id = provider_id
        self.model_id = model_id

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute a generation request deterministically."""
        # 1. Error simulation hooks
        if "[SIMULATE_TIMEOUT]" in request.prompt:
            return GenerationResponse(
                provider_job_id=f"job_to_{request.track_spec_id}",
                status=AttemptStatus.FAILED,
                error_message="Provider request timed out after 30.0s",
            )

        if "[SIMULATE_QUOTA_EXCEEDED]" in request.prompt:
            return GenerationResponse(
                provider_job_id=f"job_quota_{request.track_spec_id}",
                status=AttemptStatus.FAILED,
                error_message="Provider monthly credit quota exceeded",
            )

        if "[SIMULATE_CONTENT_FLAG]" in request.prompt:
            return GenerationResponse(
                provider_job_id=f"job_flag_{request.track_spec_id}",
                status=AttemptStatus.FAILED,
                error_message="Prompt flagged by safety filters",
            )

        # 2. Synthesize audio
        # Clamp duration for mock tests (e.g. generate 5 seconds to keep tests fast, or actual)
        duration = min(float(request.duration_seconds), 30.0)
        audio_bytes = synthesize_mock_wav(
            duration_seconds=duration,
            sample_rate=44100,
            channels=2,
            seed=request.seed,
        )

        job_id = f"job_mock_{request.track_spec_id}_{request.seed}"
        return GenerationResponse(
            provider_job_id=job_id,
            status=AttemptStatus.SUCCEEDED,
            audio_data=audio_bytes,
            mime_type="audio/wav",
            actual_duration_seconds=duration,
            cost_credits=1.0,
        )

    def check_health(self) -> bool:
        """Mock provider is always healthy in-process."""
        return True

    def get_quota_status(self) -> dict[str, Any]:
        """Mock provider has infinite quota."""
        return {
            "provider_id": self.provider_id,
            "credits_remaining": 999999,
            "quota_exhausted": False,
        }


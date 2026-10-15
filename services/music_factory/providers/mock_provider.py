"""Deterministic mock AI music generation provider for unit/integration testing (§4.4, §111)."""

from __future__ import annotations

import io
import math
import random
import struct
import wave
from typing import Any

from plm_contracts import CommercialUseEligibility
from plm_contracts.seams.c4 import ProviderLicenseTerms
from plm_platform.clock import stable_seed

from ..domain.enums import AttemptStatus
from ..domain.models import GenerationRequest, GenerationResponse

__all__ = ["MockMusicProvider", "synthesize_mock_wav"]




_MOCK_WORDS = (
    "neon", "rain", "window", "midnight", "signal", "harbor", "static", "lantern", "river",
    "echo", "velvet", "corridor", "amber", "orbit", "paper", "glass", "drift", "ember",
    "silver", "station", "hollow", "compass", "meadow", "cinder", "tide", "lattice",
)


def mock_lyrics(seed: int, *, lines: int = 8, words_per_line: int = 6) -> str:
    """Deterministic placeholder lyrics for a vocal mock track, so P5's lyric screening
    runs on mock output exactly as it would on a provider's lyrics."""
    rng = random.Random(stable_seed("p4.mock_lyrics", seed))
    return "\n".join(
        " ".join(rng.choice(_MOCK_WORDS) for _ in range(words_per_line)) for _ in range(lines)
    )


MOCK_MODEL_VERSION = "mock-sine-synth-1"
"""The mock is deterministic code, so its version is its synthesis algorithm's."""

def synthesize_mock_wav(
    *,
    duration_seconds: float = 5.0,
    sample_rate: int = 44100,
    channels: int = 2,
    frequency: float = 440.0,
    seed: int = 0,
) -> bytes:
    """Generate deterministic, valid PCM 16-bit WAV bytes of exactly ``duration_seconds``.

    One second of a seed-dependent sine is synthesised and its bytes are repeated to the
    full length, so a three-minute track costs one second of arithmetic. With the default
    integer frequency a second holds a whole number of cycles, so the repeat is seamless.
    Samples are packed little-endian explicitly, so the bytes do not depend on the host.
    """
    total_frames = round(duration_seconds * sample_rate)
    freq = frequency + (seed % 100)
    # About -14 LUFS (streaming reference loudness) for the mock's sine, so mock runs
    # exercise P5's loudness check the way a mastered track would, not as an outlier.
    amplitude = 7600

    block_frames = min(total_frames, sample_rate)
    samples = [
        int(amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate))
        for i in range(block_frames)
    ]
    interleaved = [sample for sample in samples for _ in range(channels)]
    block = struct.pack(f"<{len(interleaved)}h", *interleaved)

    frame_bytes = 2 * channels
    if block_frames:
        whole, rest = divmod(total_frames, block_frames)
        frames = block * whole + block[: rest * frame_bytes]
    else:
        frames = b""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)
    return buffer.getvalue()


class MockMusicProvider:
    """Mock music provider implementing deterministic synthesis and error simulation."""

    def __init__(self, *, provider_id: str = "mock", model_id: str = "mock-v1") -> None:
        self.provider_id = provider_id
        self.model_id = model_id

    def license_terms(self) -> ProviderLicenseTerms:
        """Mock output is synthesized in-process by PLM itself; PLM owns it outright."""
        return ProviderLicenseTerms(
            commercial_use_status=CommercialUseEligibility.ELIGIBLE,
            basis="plm_internal_synthesis",
            license_reference="plm-mock-provider",
            plan_or_tier="internal",
            account_identity_ref="plm-mock",
            terms_version="mock-v1",
        )

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

        # 2. Synthesize audio of exactly the requested duration (ADR-0032): a mock run must
        # exercise P5's real duration gate, not be shortened to dodge it.
        duration = float(request.duration_seconds)
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
            model_version=MOCK_MODEL_VERSION,
            lyrics_text=(
                mock_lyrics(request.seed) if request.vocal_mode.lower() != "instrumental" else None
            ),
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

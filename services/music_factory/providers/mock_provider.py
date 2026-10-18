"""Deterministic mock AI music generation provider for unit/integration testing (§4.4, §111)."""

from __future__ import annotations

import io
import random
import wave
from typing import Any, Final

import numpy as np
from plm_contracts import CommercialUseEligibility, ProviderMode
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


MOCK_MODEL_VERSION = "mock-chord-synth-2"
"""The mock is deterministic code, so its version is its synthesis algorithm's.

``mock-sine-synth-1`` was one sustained sine at ``440 + seed % 100`` Hz. P5's landmark
fingerprint (about 11 Hz per bin) sees only about ten distinct recordings in that space,
so once a few mock tracks were released every later mock candidate matched the internal
catalog (``INTERNAL_CATALOG_AUDIO_MATCH``) and a mock pipeline run could never clear
supply. Version 2 synthesises a seeded sequence of chords instead (R5)."""

_NOTE_SECONDS: Final[float] = 0.25
_LOOP_SECONDS: Final[int] = 8
_PEAK: Final[float] = 0.5
"""Peak of the loop: about -14.6 LUFS integrated (streaming reference loudness) for the
chord loop, so mock runs exercise P5's loudness check the way a mastered track would."""


def synthesize_mock_wav(
    *,
    duration_seconds: float = 5.0,
    sample_rate: int = 44100,
    channels: int = 2,
    seed: int = 0,
) -> bytes:
    """Generate deterministic, valid PCM 16-bit WAV bytes of exactly ``duration_seconds``.

    A loop of up to eight seconds is synthesised from the seed: every 250 ms a decaying
    three-note chord whose pitches (36 semitones above 110 Hz) and phases are drawn from
    a generator seeded with ``stable_seed("p4.mock_audio", seed)``. The loop is repeated
    to the full length, so a three-minute track costs eight seconds of arithmetic.
    Different seeds give different note sequences, which P5's fingerprint tells apart;
    the same seed gives the same bytes. Samples are packed little-endian explicitly.
    """
    total_frames = round(duration_seconds * sample_rate)
    rng = np.random.default_rng(stable_seed("p4.mock_audio", seed))
    loop_frames = min(total_frames, _LOOP_SECONDS * sample_rate)
    note_frames = max(1, round(_NOTE_SECONDS * sample_rate))
    t = np.arange(note_frames) / sample_rate
    envelope = np.exp(-3.0 * t)
    loop = np.zeros(loop_frames)
    for start in range(0, loop_frames, note_frames):
        length = min(note_frames, loop_frames - start)
        chord = np.zeros(length)
        for semitone in rng.integers(0, 36, size=3):
            frequency = 110.0 * 2.0 ** (float(semitone) / 12.0)
            chord += np.sin(2.0 * np.pi * frequency * t[:length] + rng.uniform(0.0, 2.0 * np.pi))
        loop[start:start + length] = envelope[:length] * chord
    peak = float(np.max(np.abs(loop))) if loop_frames else 0.0
    if peak > 0.0:
        loop = loop / peak * _PEAK
    mono = np.tile(loop, total_frames // loop_frames + 1)[:total_frames] if loop_frames         else np.zeros(0)
    pcm = np.round(mono * 32767.0).astype("<i2")
    frames = np.repeat(pcm, channels).tobytes() if channels > 1 else pcm.tobytes()

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)
    return buffer.getvalue()


class MockMusicProvider:
    """Mock music provider implementing deterministic synthesis and error simulation.

    It synthesises in-process and calls nothing outside PLM, so it declares
    ``ProviderMode.MOCK``: the write guard answers ``SIMULATE`` and no budget is needed.
    """

    provider_mode = ProviderMode.MOCK

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

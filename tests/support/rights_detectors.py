"""Controlled rights-detector inputs for P5 tests (R4, ADR-0034).

Everything here is deterministic and synthetic: audio is numpy-generated from a seed,
reference lyrics are invented text. ``configured_detectors()`` returns a fully
configured :class:`RightsDetectors` (empty internal catalog, a one-entry reference set
and a one-entry lyric corpus that are unrelated to any test candidate), so a test that
wants a candidate to clear must still supply clean audio, clean lyrics and complete
provenance; a test that wants UNKNOWN passes ``None`` for a source.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import soundfile as sf
from quality_gate.detectors import (
    InMemoryFingerprintSource,
    InMemoryLyricCorpus,
    ReferenceFingerprint,
    RightsDetectors,
    fingerprint_samples,
    load_detector_policy,
)
from quality_gate.detectors.stores import AudioFingerprintSource, LyricCorpusSource
from quality_gate.evaluators.rights import GenerationProvenanceFacts, SourceInput

__all__ = [
    "SAMPLE_RATE",
    "UNRELATED_REFERENCE_LYRIC",
    "UNSET",
    "clean_generation_facts",
    "configured_detectors",
    "reference_entry",
    "register_controlled_references",
    "synthetic_music",
    "write_wav",
]

SAMPLE_RATE = 44100

UNRELATED_REFERENCE_LYRIC = (
    "copper trains are rolling through the valley of the morning rain "
    "every lantern on the platform hums a tune we used to know "
    "carry me across the river where the quiet orchards grow"
)


class _Unset:
    pass


UNSET = _Unset()
"""Marker: use the default configured source (distinct from ``None`` = not configured)."""


def synthetic_music(seed: int, seconds: float = 6.0, sample_rate: int = SAMPLE_RATE,
                    *, peak: float = 0.5) -> npt.NDArray[np.float64]:
    """A deterministic stereo "track": three-note chords every 250 ms with decay.

    Different seeds give different note sequences (unrelated audio); the same seed gives
    identical samples on every host.
    """
    rng = np.random.default_rng(seed)
    total = int(seconds * sample_rate)
    out = np.zeros(total)
    note = int(0.25 * sample_rate)
    for start in range(0, total, note):
        length = min(note, total - start)
        t = np.arange(length) / sample_rate
        envelope = np.exp(-3.0 * t)
        for semitone in rng.integers(0, 36, size=3):
            freq = 110.0 * 2.0 ** (float(semitone) / 12.0)
            out[start : start + length] += envelope * np.sin(
                2.0 * np.pi * freq * t + rng.uniform(0.0, 2.0 * np.pi)
            )
    out = out / np.max(np.abs(out)) * peak
    return np.stack([out, out * 0.95], axis=1)


def write_wav(path: Path, samples: npt.NDArray[np.float64],
              sample_rate: int = SAMPLE_RATE) -> Path:
    sf.write(str(path), samples, sample_rate, subtype="PCM_16")
    return path


def reference_entry(ref_id: str, samples: npt.NDArray[np.float64], *,
                    candidate_id: str | None = None,
                    sample_rate: int = SAMPLE_RATE) -> ReferenceFingerprint:
    params = load_detector_policy().audio_fingerprint
    return ReferenceFingerprint(
        ref_id=ref_id,
        fingerprint=fingerprint_samples(samples, sample_rate, params),
        candidate_id=candidate_id,
        label=ref_id,
    )


def configured_detectors(
    *,
    catalog: Sequence[ReferenceFingerprint] | AudioFingerprintSource | None | _Unset = UNSET,
    references: Sequence[ReferenceFingerprint] | AudioFingerprintSource | None | _Unset = UNSET,
    lyrics: dict[str, str] | LyricCorpusSource | None | _Unset = UNSET,
) -> RightsDetectors:
    """Every source configured with controlled data unless overridden.

    Defaults: an empty internal catalog (nothing released yet: a legitimate NO_MATCH), a
    reference set holding one unrelated synthetic recording, and a lyric corpus holding
    one unrelated lyric. Pass ``None`` to leave a source unconfigured (UNKNOWN).
    """
    policy = load_detector_policy()

    def audio(value: object, default: Sequence[ReferenceFingerprint]
              ) -> AudioFingerprintSource | None:
        if isinstance(value, _Unset):
            return InMemoryFingerprintSource(default)
        if value is None or isinstance(value, AudioFingerprintSource):
            return value
        return InMemoryFingerprintSource(value)  # type: ignore[arg-type]

    if isinstance(lyrics, _Unset):
        corpus: LyricCorpusSource | None = InMemoryLyricCorpus.from_texts(
            {"ref_lyric_unrelated": UNRELATED_REFERENCE_LYRIC}, policy.lyric_similarity
        )
    elif isinstance(lyrics, dict):
        corpus = InMemoryLyricCorpus.from_texts(lyrics, policy.lyric_similarity)
    else:
        corpus = lyrics

    return RightsDetectors(
        internal_catalog=audio(catalog, ()),
        reference_audio=audio(
            references, (reference_entry("ref_unrelated", synthetic_music(9001)),)
        ),
        lyric_corpus=corpus,
    )


def clean_generation_facts(
    *, source_inputs: tuple[SourceInput, ...] = (),
    generation_timestamp: datetime = datetime(2026, 9, 25, 11, 59, tzinfo=UTC),
) -> GenerationProvenanceFacts:
    """Complete declared P28 facts: a text-to-music generation with no other inputs."""
    return GenerationProvenanceFacts(
        model_version="mock-v1.0.0",
        generation_timestamp=generation_timestamp,
        source_inputs=source_inputs,
        human_contribution="none: generated from the prompt without human editing",
    )


def register_controlled_references(engine: Any, *, now: datetime) -> None:
    """Register in the qa database the same controlled data `configured_detectors()` uses
    in memory: one unrelated synthetic recording and one unrelated lyric. Tests that go
    through P5's route (which reads the database) need a configured, non-empty reference
    set and corpus, or every evaluation is UNKNOWN and quarantines."""
    import tempfile

    from quality_gate.storage.detector_catalog import (
        register_reference_audio,
        register_reference_lyrics,
    )
    from sqlalchemy.orm import sessionmaker

    policy = load_detector_policy()
    with tempfile.TemporaryDirectory() as tmp, sessionmaker(bind=engine)() as session:
        path = write_wav(Path(tmp) / "ref_unrelated.wav", synthetic_music(seed=991))
        register_reference_audio(session, ref_id="ref_audio_unrelated", audio_path=path,
                                 source_note="synthetic test reference",
                                 params=policy.audio_fingerprint, now=now)
        register_reference_lyrics(session, ref_id="ref_lyric_unrelated",
                                  text=UNRELATED_REFERENCE_LYRIC,
                                  source_note="synthetic test lyric",
                                  params=policy.lyric_similarity, now=now)
        session.commit()

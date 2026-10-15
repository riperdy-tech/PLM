"""Running the rights detectors for one candidate (R4, ADR-0034).

:class:`RightsDetectors` is what ``run_candidate_evaluation`` is given: the internal
catalog, the reference audio set, the lyric corpus, the protected-artist policy and the
declared thresholds. Each source is injected; ``None`` means "not configured", which is
``UNKNOWN``. Any failure to read a source or the audio is ``UNKNOWN`` as well, so the
gate can only clear a candidate whose detectors actually ran and found nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from plm_platform.protected_artists import ProtectedArtistPolicy, load_protected_artist_policy

from .config import DetectorPolicy, load_detector_policy
from .fingerprint import (
    AUDIO_DETECTOR,
    AudioFingerprint,
    AudioUnreadableError,
    fingerprint_file,
    match_fingerprint,
)
from .lyric_similarity import LYRIC_DETECTOR, match_lyrics
from .outcome import DetectorOutcome, DetectorResult, DetectorUnavailableError
from .stores import AudioFingerprintSource, LyricCorpusSource

__all__ = [
    "INTERNAL_CATALOG",
    "REFERENCE_CORPUS",
    "REFERENCE_SET",
    "AudioDetection",
    "RightsDetectors",
    "detect_audio",
    "detect_lyrics",
    "is_instrumental",
]

INTERNAL_CATALOG: Final[str] = "internal_catalog"
REFERENCE_SET: Final[str] = "reference_set"
REFERENCE_CORPUS: Final[str] = "reference_corpus"


@dataclass(frozen=True)
class RightsDetectors:
    """The detector inputs injected into the gate.

    ``internal_catalog``: fingerprints of P5's own released candidates (a manifest was
    issued). ``reference_audio``: the configured reference recordings. ``lyric_corpus``:
    the configured reference lyrics. Pass ``None`` for a source that is not configured;
    the gate records ``UNKNOWN`` for it and quarantines.
    """

    internal_catalog: AudioFingerprintSource | None
    reference_audio: AudioFingerprintSource | None
    lyric_corpus: LyricCorpusSource | None
    protected_artists: ProtectedArtistPolicy = field(default_factory=load_protected_artist_policy)
    policy: DetectorPolicy = field(default_factory=load_detector_policy)

    @classmethod
    def unconfigured(cls) -> RightsDetectors:
        """No source configured: every detector is UNKNOWN (fail closed)."""
        return cls(internal_catalog=None, reference_audio=None, lyric_corpus=None)


@dataclass(frozen=True, slots=True)
class AudioDetection:
    """The candidate's fingerprint (None when the audio was unreadable) and the results."""

    fingerprint: AudioFingerprint | None
    results: tuple[DetectorResult, ...]


def _unknown(detector: str, source: str, version: str, thresholds: str, reason: str,
             **metrics: object) -> DetectorResult:
    return DetectorResult(
        detector=detector,
        source=source,
        outcome=DetectorOutcome.UNKNOWN,
        version=version,
        thresholds_version=thresholds,
        reason=reason,
        metrics=dict(metrics),
    )


def _source_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, DetectorUnavailableError):
        return "source_unavailable", str(exc)
    return f"source_error:{type(exc).__name__}", str(exc)


def detect_audio(
    audio_path: str | Path, *, candidate_id: str, detectors: RightsDetectors
) -> AudioDetection:
    """Fingerprint the candidate and match it against the catalog and reference set."""
    params = detectors.policy.audio_fingerprint
    version = params.fingerprint_version
    thresholds = detectors.policy.policy_version
    sources: tuple[tuple[str, AudioFingerprintSource | None, bool], ...] = (
        (INTERNAL_CATALOG, detectors.internal_catalog, False),
        (REFERENCE_SET, detectors.reference_audio, True),
    )
    try:
        candidate = fingerprint_file(audio_path, params)
    except AudioUnreadableError as exc:
        return AudioDetection(
            fingerprint=None,
            results=tuple(
                _unknown(AUDIO_DETECTOR, name, version, thresholds, "audio_unreadable",
                         error=str(exc))
                for name, _, _ in sources
            ),
        )

    results: list[DetectorResult] = []
    for name, source, empty_is_unknown in sources:
        if source is None:
            results.append(_unknown(AUDIO_DETECTOR, name, version, thresholds, "not_configured"))
            continue
        try:
            snapshot = source.load_fingerprints(exclude_candidate_id=candidate_id)
        except Exception as exc:  # noqa: BLE001 - fail closed: any read failure is UNKNOWN
            reason, detail = _source_failure(exc)
            results.append(_unknown(AUDIO_DETECTOR, name, version, thresholds, reason,
                                    error=detail))
            continue
        results.append(
            match_fingerprint(
                candidate,
                snapshot.entries,
                params,
                source=name,
                thresholds_version=thresholds,
                snapshot_ref=snapshot.snapshot_ref,
                empty_source_is_unknown=empty_is_unknown,
            )
        )
    return AudioDetection(fingerprint=candidate, results=tuple(results))


def is_instrumental(vocal_mode: str) -> bool:
    return vocal_mode.strip().lower() == "instrumental"


def detect_lyrics(
    lyrics_text: str | None, *, vocal_mode: str, detectors: RightsDetectors
) -> DetectorResult:
    """Lyric similarity against the corpus.

    No lyric text on an instrumental is ``NOT_APPLICABLE``. No lyric text on a vocal
    track is ``UNKNOWN``: there are words, P5 just cannot see them.
    """
    params = detectors.policy.lyric_similarity
    version = params.corpus_version
    thresholds = detectors.policy.policy_version
    if lyrics_text is None or not lyrics_text.strip():
        if is_instrumental(vocal_mode):
            return DetectorResult(
                detector=LYRIC_DETECTOR,
                source=REFERENCE_CORPUS,
                outcome=DetectorOutcome.NOT_APPLICABLE,
                version=version,
                thresholds_version=thresholds,
                reason="instrumental_without_lyrics",
            )
        return _unknown(LYRIC_DETECTOR, REFERENCE_CORPUS, version, thresholds,
                        "vocal_track_without_lyrics_text", vocal_mode=vocal_mode)
    if detectors.lyric_corpus is None:
        return _unknown(LYRIC_DETECTOR, REFERENCE_CORPUS, version, thresholds, "not_configured")
    try:
        corpus = detectors.lyric_corpus.load_lyric_corpus()
    except Exception as exc:  # noqa: BLE001 - fail closed: any read failure is UNKNOWN
        reason, detail = _source_failure(exc)
        return _unknown(LYRIC_DETECTOR, REFERENCE_CORPUS, version, thresholds, reason,
                        error=detail)
    return match_lyrics(
        lyrics_text,
        corpus.entries,
        params,
        source=REFERENCE_CORPUS,
        thresholds_version=thresholds,
        snapshot_ref=corpus.snapshot_ref,
    )

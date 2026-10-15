"""Where the detectors' references come from: injected sources (R4, ADR-0034).

The gate never reaches for a global catalog: ``run_candidate_evaluation`` is given
these sources, so tests use controlled data and production wires the database tables
(:mod:`quality_gate.storage.detector_catalog`) or a reference directory.

A source raises :class:`~quality_gate.detectors.outcome.DetectorUnavailableError` when it
cannot be read; the gate then records ``UNKNOWN`` and quarantines. A source that is not
configured at all is passed as ``None`` and is ``UNKNOWN`` too, never clearance.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from .config import AudioFingerprintParams, LyricSimilarityParams
from .fingerprint import (
    AudioUnreadableError,
    ReferenceFingerprint,
    fingerprint_file,
    fingerprint_set_ref,
)
from .lyric_similarity import LyricReference, corpus_ref, lyric_shingles
from .outcome import DetectorUnavailableError

__all__ = [
    "MAX_REF_ID_LENGTH",
    "AudioFingerprintSource",
    "DirectoryLyricCorpus",
    "DirectoryReferenceAudioSet",
    "FingerprintSet",
    "InMemoryFingerprintSource",
    "InMemoryLyricCorpus",
    "LyricCorpus",
    "LyricCorpusSource",
]

MAX_REF_ID_LENGTH: Final[int] = 64
"""``qa.rights_checks.matched_reference_id`` is 64 characters; longer ids are refused."""

AUDIO_SUFFIXES: Final[tuple[str, ...]] = (".wav", ".flac", ".ogg")


def _check_ids(ids: Sequence[str]) -> None:
    long_ids = [i for i in ids if not i or len(i) > MAX_REF_ID_LENGTH]
    if long_ids:
        raise DetectorUnavailableError(
            f"reference ids must be 1-{MAX_REF_ID_LENGTH} characters: {long_ids[:5]}"
        )
    if len(set(ids)) != len(ids):
        raise DetectorUnavailableError("a reference id appears twice in one source")


@dataclass(frozen=True, slots=True)
class FingerprintSet:
    """A snapshot of one fingerprint source, with its content hash."""

    snapshot_ref: str
    entries: tuple[ReferenceFingerprint, ...]

    @classmethod
    def of(cls, entries: Sequence[ReferenceFingerprint]) -> FingerprintSet:
        _check_ids([e.ref_id for e in entries])
        ordered = tuple(sorted(entries, key=lambda e: e.ref_id))
        return cls(snapshot_ref=fingerprint_set_ref(ordered), entries=ordered)


@dataclass(frozen=True, slots=True)
class LyricCorpus:
    snapshot_ref: str
    entries: tuple[LyricReference, ...]

    @classmethod
    def of(cls, entries: Sequence[LyricReference]) -> LyricCorpus:
        _check_ids([e.ref_id for e in entries])
        ordered = tuple(sorted(entries, key=lambda e: e.ref_id))
        return cls(snapshot_ref=corpus_ref(ordered), entries=ordered)


@runtime_checkable
class AudioFingerprintSource(Protocol):
    """Internal catalog or reference set of audio fingerprints."""

    def load_fingerprints(self, *, exclude_candidate_id: str | None) -> FingerprintSet:
        """Every entry, minus the candidate's own (re-evaluation must not self-match).

        Raises :class:`DetectorUnavailableError` when the source cannot be read.
        """
        ...


@runtime_checkable
class LyricCorpusSource(Protocol):
    def load_lyric_corpus(self) -> LyricCorpus:
        """Every reference; raises :class:`DetectorUnavailableError` when unreadable."""
        ...


class InMemoryFingerprintSource:
    """A fixed list of fingerprints (tests, and callers that already hold them)."""

    def __init__(self, entries: Sequence[ReferenceFingerprint]) -> None:
        self._entries = tuple(entries)

    def load_fingerprints(self, *, exclude_candidate_id: str | None) -> FingerprintSet:
        return FingerprintSet.of(
            [
                e
                for e in self._entries
                if exclude_candidate_id is None or e.candidate_id != exclude_candidate_id
            ]
        )


class InMemoryLyricCorpus:
    def __init__(self, entries: Sequence[LyricReference]) -> None:
        self._entries = tuple(entries)

    def load_lyric_corpus(self) -> LyricCorpus:
        return LyricCorpus.of(self._entries)

    @classmethod
    def from_texts(
        cls, texts: dict[str, str], params: LyricSimilarityParams
    ) -> InMemoryLyricCorpus:
        return cls(
            [
                LyricReference(
                    ref_id=ref_id,
                    corpus_version=params.corpus_version,
                    shingles=lyric_shingles(text, params.shingle_size),
                )
                for ref_id, text in sorted(texts.items())
            ]
        )


class DirectoryReferenceAudioSet:
    """Reference recordings in a directory (``*.wav``, ``*.flac``, ``*.ogg``).

    Each file is one reference, ``ref_id`` = the file stem. A missing directory or an
    unreadable file makes the source unavailable (it is not skipped: a silently smaller
    reference set would look like a clean check).
    """

    def __init__(self, directory: str | Path, params: AudioFingerprintParams) -> None:
        self._directory = Path(directory)
        self._params = params

    def load_fingerprints(self, *, exclude_candidate_id: str | None) -> FingerprintSet:
        if not self._directory.is_dir():
            raise DetectorUnavailableError(f"reference directory {self._directory} is missing")
        entries: list[ReferenceFingerprint] = []
        for path in sorted(self._directory.iterdir()):
            if path.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            try:
                fp = fingerprint_file(path, self._params)
            except AudioUnreadableError as exc:
                raise DetectorUnavailableError(f"reference {path.name}: {exc}") from exc
            entries.append(ReferenceFingerprint(ref_id=path.stem, fingerprint=fp))
        return FingerprintSet.of(entries)


class DirectoryLyricCorpus:
    """Reference lyrics in a directory (``*.txt``, UTF-8), ``ref_id`` = file stem."""

    def __init__(self, directory: str | Path, params: LyricSimilarityParams) -> None:
        self._directory = Path(directory)
        self._params = params

    def load_lyric_corpus(self) -> LyricCorpus:
        if not self._directory.is_dir():
            raise DetectorUnavailableError(f"lyric corpus directory {self._directory} is missing")
        entries: list[LyricReference] = []
        for path in sorted(self._directory.glob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise DetectorUnavailableError(f"corpus entry {path.name}: {exc}") from exc
            entries.append(
                LyricReference(
                    ref_id=path.stem,
                    corpus_version=self._params.corpus_version,
                    shingles=lyric_shingles(text, self._params.shingle_size),
                )
            )
        return LyricCorpus.of(entries)

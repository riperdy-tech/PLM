"""Detector catalog tables and their sources (R4, ADR-0034; migration 0031).

* ``qa.candidate_audio_fingerprints``: the fingerprint of every evaluated candidate,
  written with the evaluation. The **internal catalog** is the subset whose evaluation
  issued a release manifest (passed, or cleared later by an audited override), so a
  candidate joins the catalog exactly when a manifest exists for it.
* ``qa.reference_audio_fingerprints``: the configured reference recordings.
* ``qa.reference_lyrics``: the configured lyric corpus, stored as shingle hashes only
  (no lyric text is kept).

Reference rows are registered by an operator with a ``source_note`` saying where the
reference came from; one row per ``(ref_id, version)``, so re-registering under a new
fingerprint or corpus version keeps the old row for audit.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..detectors.config import AudioFingerprintParams, LyricSimilarityParams
from ..detectors.fingerprint import AudioFingerprint, ReferenceFingerprint, fingerprint_file
from ..detectors.lyric_similarity import LyricReference, lyric_shingles
from ..detectors.stores import MAX_REF_ID_LENGTH, FingerprintSet, LyricCorpus
from ..domain.ids import deterministic_id
from .models import QA_METADATA, release_manifests_table

__all__ = [
    "SqlInternalCatalog",
    "SqlLyricCorpus",
    "SqlReferenceAudioSet",
    "candidate_audio_fingerprints_table",
    "insert_candidate_fingerprint",
    "reference_audio_fingerprints_table",
    "reference_lyrics_table",
    "register_reference_audio",
    "register_reference_lyrics",
]

candidate_audio_fingerprints_table = sa.Table(
    "candidate_audio_fingerprints",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, unique=True),
    sa.Column("candidate_id", sa.String(64), nullable=False, index=True),
    sa.Column("artifact_sha256", sa.String(64), nullable=False),
    sa.Column("fingerprint_version", sa.String(64), nullable=False),
    sa.Column("hash_count", sa.Integer, nullable=False),
    sa.Column("duration_seconds", sa.Float, nullable=False),
    sa.Column("fingerprint", sa.LargeBinary, nullable=False),
    sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

reference_audio_fingerprints_table = sa.Table(
    "reference_audio_fingerprints",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("ref_id", sa.String(64), nullable=False, index=True),
    sa.Column("label", sa.String(256), nullable=True),
    sa.Column("source_note", sa.Text, nullable=False),
    sa.Column("fingerprint_version", sa.String(64), nullable=False),
    sa.Column("hash_count", sa.Integer, nullable=False),
    sa.Column("duration_seconds", sa.Float, nullable=False),
    sa.Column("fingerprint", sa.LargeBinary, nullable=False),
    sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
    sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("ref_id", "fingerprint_version", name="uq_reference_audio_ref_version"),
)

reference_lyrics_table = sa.Table(
    "reference_lyrics",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("ref_id", sa.String(64), nullable=False, index=True),
    sa.Column("label", sa.String(256), nullable=True),
    sa.Column("source_note", sa.Text, nullable=False),
    sa.Column("corpus_version", sa.String(64), nullable=False),
    sa.Column("shingle_count", sa.Integer, nullable=False),
    sa.Column("shingles", sa.LargeBinary, nullable=False),
    sa.Column("shingles_sha256", sa.String(64), nullable=False),
    sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("ref_id", "corpus_version", name="uq_reference_lyrics_ref_version"),
)


def _check_ref_id(ref_id: str) -> None:
    if not ref_id or len(ref_id) > MAX_REF_ID_LENGTH:
        raise ValueError(f"ref_id must be 1-{MAX_REF_ID_LENGTH} characters")


def _shingles_to_bytes(shingles: frozenset[int]) -> bytes:
    return b"".join(value.to_bytes(8, "big") for value in sorted(shingles))


def _shingles_from_bytes(blob: bytes) -> frozenset[int]:
    if len(blob) % 8:
        raise ValueError("shingle blob is not a whole number of 64-bit hashes")
    return frozenset(int.from_bytes(blob[i : i + 8], "big") for i in range(0, len(blob), 8))


# ------------------------------------------------------------------------ writers


def insert_candidate_fingerprint(
    session: Session,
    *,
    evaluation_id: str,
    candidate_id: str,
    artifact_sha256: str,
    fingerprint: AudioFingerprint,
    now: datetime,
) -> None:
    """Store the candidate's fingerprint with its evaluation (one row per evaluation)."""
    session.execute(
        candidate_audio_fingerprints_table.insert().values(
            id=deterministic_id("cafp", evaluation_id),
            evaluation_id=evaluation_id,
            candidate_id=candidate_id,
            artifact_sha256=artifact_sha256,
            fingerprint_version=fingerprint.version,
            hash_count=fingerprint.hash_count,
            duration_seconds=fingerprint.duration_seconds,
            fingerprint=fingerprint.to_bytes(),
            fingerprint_sha256=fingerprint.digest(),
            created_at=now,
        )
    )


def register_reference_audio(
    session: Session,
    *,
    ref_id: str,
    audio_path: str | Path,
    source_note: str,
    params: AudioFingerprintParams,
    now: datetime,
    label: str | None = None,
) -> AudioFingerprint:
    """Fingerprint a reference recording and add it to the reference set."""
    _check_ref_id(ref_id)
    if not source_note.strip():
        raise ValueError("source_note (where the reference came from) is required")
    fingerprint = fingerprint_file(audio_path, params)
    session.execute(
        reference_audio_fingerprints_table.insert().values(
            id=deterministic_id("rafp", ref_id, fingerprint.version),
            ref_id=ref_id,
            label=label,
            source_note=source_note,
            fingerprint_version=fingerprint.version,
            hash_count=fingerprint.hash_count,
            duration_seconds=fingerprint.duration_seconds,
            fingerprint=fingerprint.to_bytes(),
            fingerprint_sha256=fingerprint.digest(),
            registered_at=now,
        )
    )
    return fingerprint


def register_reference_lyrics(
    session: Session,
    *,
    ref_id: str,
    text: str,
    source_note: str,
    params: LyricSimilarityParams,
    now: datetime,
    label: str | None = None,
) -> LyricReference:
    """Add one reference lyric to the corpus as shingle hashes (the text is not kept)."""
    _check_ref_id(ref_id)
    if not source_note.strip():
        raise ValueError("source_note (where the reference came from) is required")
    reference = LyricReference(
        ref_id=ref_id,
        corpus_version=params.corpus_version,
        shingles=lyric_shingles(text, params.shingle_size),
        label=label,
    )
    if not reference.shingles:
        raise ValueError("the reference lyric is too short to produce any shingle")
    blob = _shingles_to_bytes(reference.shingles)
    session.execute(
        reference_lyrics_table.insert().values(
            id=deterministic_id("rlyr", ref_id, reference.corpus_version),
            ref_id=ref_id,
            label=label,
            source_note=source_note,
            corpus_version=reference.corpus_version,
            shingle_count=len(reference.shingles),
            shingles=blob,
            shingles_sha256=hashlib.sha256(blob).hexdigest(),
            registered_at=now,
        )
    )
    return reference


# ------------------------------------------------------------------------ sources


def _current_or_stale(
    rows: list[sa.RowMapping], version_column: str, current: str
) -> list[sa.RowMapping]:
    """Per ``ref_id``: the row of the current version, else one stale row.

    A stale-only reference is returned so the matcher reports the version mismatch
    (UNKNOWN) instead of silently dropping that reference.
    """
    by_ref: dict[str, list[sa.RowMapping]] = defaultdict(list)
    for row in rows:
        by_ref[row["ref_id"]].append(row)
    chosen: list[sa.RowMapping] = []
    for ref_id in sorted(by_ref):
        group = by_ref[ref_id]
        current_rows = [r for r in group if r[version_column] == current]
        chosen.append(
            current_rows[0] if current_rows else min(group, key=lambda r: r[version_column])
        )
    return chosen


class SqlInternalCatalog:
    """P5's released candidates: fingerprints whose evaluation issued a manifest."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_fingerprints(self, *, exclude_candidate_id: str | None) -> FingerprintSet:
        fp = candidate_audio_fingerprints_table
        stmt = (
            sa.select(fp)
            .where(
                sa.exists().where(release_manifests_table.c.evaluation_id == fp.c.evaluation_id)
            )
            .order_by(fp.c.evaluation_id)
        )
        if exclude_candidate_id is not None:
            stmt = stmt.where(fp.c.candidate_id != exclude_candidate_id)
        rows = self._session.execute(stmt).mappings().all()
        return FingerprintSet.of(
            [
                ReferenceFingerprint(
                    ref_id=row["evaluation_id"],
                    fingerprint=AudioFingerprint.from_bytes(
                        row["fingerprint_version"], row["fingerprint"], row["duration_seconds"]
                    ),
                    candidate_id=row["candidate_id"],
                    label=row["candidate_id"],
                )
                for row in rows
            ]
        )


class SqlReferenceAudioSet:
    """The registered reference recordings (``qa.reference_audio_fingerprints``)."""

    def __init__(self, session: Session, params: AudioFingerprintParams) -> None:
        self._session = session
        self._version = params.fingerprint_version

    def load_fingerprints(self, *, exclude_candidate_id: str | None) -> FingerprintSet:
        del exclude_candidate_id  # references are never candidates
        rows = list(
            self._session.execute(sa.select(reference_audio_fingerprints_table)).mappings().all()
        )
        return FingerprintSet.of(
            [
                ReferenceFingerprint(
                    ref_id=row["ref_id"],
                    fingerprint=AudioFingerprint.from_bytes(
                        row["fingerprint_version"], row["fingerprint"], row["duration_seconds"]
                    ),
                    label=row["label"],
                )
                for row in _current_or_stale(rows, "fingerprint_version", self._version)
            ]
        )


class SqlLyricCorpus:
    """The registered reference lyrics (``qa.reference_lyrics``)."""

    def __init__(self, session: Session, params: LyricSimilarityParams) -> None:
        self._session = session
        self._version = params.corpus_version

    def load_lyric_corpus(self) -> LyricCorpus:
        rows = list(self._session.execute(sa.select(reference_lyrics_table)).mappings().all())
        return LyricCorpus.of(
            [
                LyricReference(
                    ref_id=row["ref_id"],
                    corpus_version=row["corpus_version"],
                    shingles=_shingles_from_bytes(row["shingles"]),
                    label=row["label"],
                )
                for row in _current_or_stale(rows, "corpus_version", self._version)
            ]
        )

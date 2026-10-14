"""The result vocabulary shared by P5's rights detectors (R4, ADR-0034)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = ["DetectorOutcome", "DetectorResult", "DetectorUnavailableError"]


class DetectorOutcome(StrEnum):
    """What one detector concluded about one candidate against one source.

    ``UNKNOWN`` means the detector could not conclude (unavailable source, unreadable
    input, nothing to compare against, too little signal). It is never clearance: the
    gate quarantines on it. ``NOT_APPLICABLE`` means there is nothing of that kind to
    check (an instrumental with no lyrics) and is distinct from ``UNKNOWN``.
    """

    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DetectorUnavailableError(RuntimeError):
    """A detector source (catalog, reference set, corpus) cannot be read."""


@dataclass(frozen=True, slots=True)
class DetectorResult:
    """One detector's verdict against one source, with the numbers behind it."""

    detector: str
    """e.g. ``audio_fingerprint``, ``lyric_similarity``."""
    source: str
    """e.g. ``internal_catalog``, ``reference_set``, ``reference_corpus``."""
    outcome: DetectorOutcome
    version: str
    """The detector/fingerprint version the result was computed under."""
    thresholds_version: str
    score: float | None = None
    matched_ref: str | None = None
    source_snapshot_ref: str | None = None
    """Content hash of the reference set compared against (None when it was not read)."""
    reason: str | None = None
    """Why the outcome is UNKNOWN or NOT_APPLICABLE (machine-readable token)."""
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "source": self.source,
            "outcome": self.outcome.value,
            "version": self.version,
            "thresholds_version": self.thresholds_version,
            "score": self.score,
            "matched_ref": self.matched_ref,
            "source_snapshot_ref": self.source_snapshot_ref,
            "reason": self.reason,
            "metrics": dict(sorted(self.metrics.items())),
        }

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON of :meth:`as_dict` (the evidence hash)."""
        encoded = json.dumps(self.as_dict(), sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

"""Canonical vocabulary for the PLM platform — the shared words the nine phases drift on.

Every value below is copied verbatim from the phase spec named beside it; do not rename
or re-case. Where two phases disagreed, the master plan's rulings decide (R4 for
allocation/exploration, R7 for gate wording).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    "AllocationType",
    "ChannelActivationState",
    "CommercialUseEligibility",
    "DetectorResult",
    "GateDisposition",
    "GateOutcome",
    "GateOverrideStatus",
    "ObservationWindow",
    "PolicyAction",
    "ProvenanceStatus",
    "QualityStatus",
    "ReviewResult",
    "SourceFreshness",
    "from_exploration_level",
    "to_exploration_level",
]


class GateDisposition(StrEnum):
    """P5 §9 / §51 terminal gate decisions.

    A disposition is exactly one of these four; ``QUARANTINED`` and ``REJECTED`` stay
    distinct (P5 §9).
    """

    PASSED = "PASSED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


class GateOverrideStatus(StrEnum):
    """P5 §125: a human override sets a new *effective* status — it is not a fifth disposition."""

    CLEARED_BY_OVERRIDE = "CLEARED_BY_OVERRIDE"


class DetectorResult(StrEnum):
    """P5 §101 detector availability semantics ("These are not equivalent.")."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"


class PolicyAction(StrEnum):
    """P5 §44 rule actions."""

    PASS = "PASS"
    WARN = "WARN"
    QUARANTINE = "QUARANTINE"
    REJECT = "REJECT"
    REQUEST_RECHECK = "REQUEST_RECHECK"


class ReviewResult(StrEnum):
    """P5 §73 human review results."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_REFINEMENT = "REQUEST_REFINEMENT"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    ESCALATE = "ESCALATE"


class CommercialUseEligibility(StrEnum):
    """P5 §34 commercial-use eligibility model ("Never collapse them to Boolean.")."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"
    CONDITIONAL = "conditional"
    EXPIRED_OR_UNVERIFIED = "expired_or_unverified"


class ProvenanceStatus(StrEnum):
    """P5 §37 provenance levels."""

    COMPLETE = "COMPLETE"
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"


class QualityStatus(StrEnum):
    """P9 §57 data-quality states for every metric dataset."""

    VALID = "VALID"
    PARTIAL = "PARTIAL"
    LATE = "LATE"
    ESTIMATED = "ESTIMATED"
    REVISED = "REVISED"
    ANOMALOUS = "ANOMALOUS"
    QUARANTINED = "QUARANTINED"
    INVALID = "INVALID"


class SourceFreshness(StrEnum):
    """P9 §60 freshness scores (thresholds are provider/configuration specific)."""

    FRESH = "FRESH"
    ACCEPTABLE = "ACCEPTABLE"
    STALE = "STALE"
    CRITICAL = "CRITICAL"


class ObservationWindow(StrEnum):
    """P2 §18 reward windows also used by P9; ``lifetime`` is unbounded by definition."""

    H24 = "24h"
    H72 = "72h"
    D7 = "7d"
    D28 = "28d"
    D90 = "90d"
    LIFETIME = "lifetime"

    @property
    def duration_hours(self) -> float | None:
        """Hours the window covers as data; ``None`` means unbounded, not missing.

        ``lifetime`` is deliberately unbounded rather than a very large number: a number
        would invite arithmetic against a window that has no end.
        """
        return _WINDOW_DURATION_HOURS[self]

    @property
    def is_unbounded(self) -> bool:
        return self is ObservationWindow.LIFETIME


_WINDOW_DURATION_HOURS: Final[Mapping[ObservationWindow, float | None]] = {
    ObservationWindow.H24: 24.0,
    ObservationWindow.H72: 72.0,
    ObservationWindow.D7: 168.0,
    ObservationWindow.D28: 672.0,
    ObservationWindow.D90: 2160.0,
    ObservationWindow.LIFETIME: None,
}


class AllocationType(StrEnum):
    """P2 §34 allocation classes — canonical per ruling R4 (P3's adapter maps from these)."""

    EXPLOIT = "exploit"
    ADJACENT_EXPLORE = "adjacent_explore"
    RADICAL_EXPLORE = "radical_explore"


class ChannelActivationState(StrEnum):
    """P2 §117 channel activation states."""

    DEVELOPMENT = "DEVELOPMENT"
    DRY_RUN = "DRY_RUN"
    SHADOW = "SHADOW"
    AUTONOMOUS = "AUTONOMOUS"
    SUSPENDED = "SUSPENDED"


def to_exploration_level(allocation: AllocationType) -> str:
    """Ruling R4 (seam C2): P3's ``exploration_level`` receives the canonical P2 class.

    P3's adapter field is ``exploration_level``; P2's tokens are canonical. P3 §10 does not
    enumerate a separate token set, so the map is the identity and this function is the one
    place to change if the W2.0 C2 freeze picks different tokens.
    """
    return allocation.value


def from_exploration_level(level: str) -> AllocationType:
    """Inverse of :func:`to_exploration_level`; raises ``ValueError`` for unknown tokens."""
    return AllocationType(level)


class GateOutcome(BaseModel):
    """P5 §125: the automated disposition plus its optional human override.

    The two can never collapse: ``disposition`` must be exactly one of P5 §9's four values
    (``CLEARED_BY_OVERRIDE`` is rejected by the type itself — it is not a fifth
    disposition), and an override may only accompany a ``QUARANTINED`` or ``REJECTED``
    disposition (R7's "audited override excepted"). The original automated finding is kept
    untouched; P5 owns the audit trail around it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: GateDisposition
    override_status: GateOverrideStatus | None = None

    @model_validator(mode="after")
    def _override_only_clears_quarantine_or_rejection(self) -> Self:
        if self.override_status is not None and self.disposition not in (
            GateDisposition.QUARANTINED,
            GateDisposition.REJECTED,
        ):
            raise ValueError(
                "CLEARED_BY_OVERRIDE may only accompany a QUARANTINED or REJECTED "
                f"disposition, not {self.disposition.value!r}: an override clears a "
                "blocked candidate, it does not rewrite a passing one"
            )
        return self

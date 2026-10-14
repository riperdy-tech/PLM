"""P2 vocabulary. Every value is copied from PHASE-P2 or a frozen ruling; storage keeps text only."""

from __future__ import annotations

from enum import StrEnum

from plm_contracts.seams.c7 import ObservationMaturity as _SharedObservationMaturity

__all__ = [
    "AllocationCycleStatus", "ArmLifecycleStatus", "DirectiveStatus", "ObservationMaturity",
    "OverrideAction", "ReasonCode", "UpdateStatus",
]


class ArmLifecycleStatus(StrEnum):
    """§9's seven arm states. P2 owns these (R15); P1's own arm status is separate."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    PAUSED = "paused"
    RETIRED = "retired"
    BLOCKED = "blocked"
    ARCHIVED = "archived"


class AllocationCycleStatus(StrEnum):
    """§59: too few eligible arms makes a cycle `partial`, never `completed`."""

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class DirectiveStatus(StrEnum):
    """§52's statuses. P2 only ever writes `queued`; later phases advance the rest."""

    QUEUED = "queued"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


ObservationMaturity = _SharedObservationMaturity
"""§18/§19 maturity; defined once in ``plm_contracts.seams.c7`` (C7 leg 2)."""


class UpdateStatus(StrEnum):
    """§74's persisted update state; `applied` is unique per reward observation.

    ADR-0031: only one outcome per (experiment, content) is `applied`, the mature one at
    the policy's outcome horizon. Every other stored result is recorded with the reason
    it did not move the posterior.
    """

    APPLIED = "applied"
    PROVISIONAL = "provisional"
    OFF_HORIZON = "off_horizon"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class ReasonCode(StrEnum):
    """§57's examples (never prose-only) plus the codes this build must emit."""

    # §57 examples, verbatim
    STRONG_POSTERIOR = "STRONG_POSTERIOR"
    HIGH_UNCERTAINTY = "HIGH_UNCERTAINTY"
    MARKET_SIGNAL_STRONG = "MARKET_SIGNAL_STRONG"
    ADJACENT_TO_WINNER = "ADJACENT_TO_WINNER"
    RADICAL_EXPLORATION = "RADICAL_EXPLORATION"
    BASELINE_CONTROL = "BASELINE_CONTROL"
    LOW_FATIGUE = "LOW_FATIGUE"
    RECOVERING_MARKET_SIGNAL = "RECOVERING_MARKET_SIGNAL"
    DIVERSITY_CONSTRAINT = "DIVERSITY_CONSTRAINT"
    # forced by rulings and the spec's own failure paths
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"  # ADR-0023 #5: <2 measured components
    MARKET_STALE = "MARKET_STALE"                    # §83
    MARKET_UNAVAILABLE = "MARKET_UNAVAILABLE"        # §102: degraded cycle, never invented data
    MARKET_REGIME_CHANGE_SUSPECTED = "MARKET_REGIME_CHANGE_SUSPECTED"  # §28/§85
    INSUFFICIENT_ELIGIBLE_ARMS = "INSUFFICIENT_ELIGIBLE_ARMS"          # §59
    PROVIDER_CAPABILITY_UNAVAILABLE = "PROVIDER_CAPABILITY_UNAVAILABLE"  # §82
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"              # §107
    FATIGUE_COOLDOWN = "FATIGUE_COOLDOWN"            # §26
    WEAK_POSTERIOR = "WEAK_POSTERIOR"                # §38 retirement evidence


class OverrideAction(StrEnum):
    """§107's explicit operator events; §108 gives them an expiry."""

    PAUSE_ARM = "pause_arm"
    FORCE_ARM = "force_arm"

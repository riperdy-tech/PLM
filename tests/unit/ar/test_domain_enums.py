# tests/unit/ar/test_domain_enums.py
from services.autonomous_ar.domain.enums import (
    AllocationCycleStatus,
    ArmLifecycleStatus,
    DirectiveStatus,
    ObservationMaturity,
    ReasonCode,
    UpdateStatus,
)


def test_lifecycle_and_cycle_statuses_match_the_spec() -> None:
    assert {s.value for s in ArmLifecycleStatus} == {
        "candidate", "active", "cooldown", "paused", "retired", "blocked", "archived",
    }
    assert {s.value for s in AllocationCycleStatus} == {
        "planned", "running", "completed", "partial", "failed",
    }
    assert {s.value for s in DirectiveStatus} == {
        "queued", "accepted", "in_progress", "completed", "cancelled", "expired", "failed",
    }
    assert {s.value for s in ObservationMaturity} == {"provisional", "mature", "closed"}
    assert {s.value for s in UpdateStatus} == {
        "applied", "provisional", "off_horizon", "skipped_duplicate", "quarantined",
        "failed",
    }


def test_reason_codes_are_the_spec_nine_plus_the_nine_this_build_requires() -> None:
    # §57's nine, then the codes ADR-0023 and §59/§82/§83/§102/§107 force this phase to emit.
    assert {r.value for r in ReasonCode} == {
        "STRONG_POSTERIOR", "HIGH_UNCERTAINTY", "MARKET_SIGNAL_STRONG",
        "ADJACENT_TO_WINNER", "RADICAL_EXPLORATION", "BASELINE_CONTROL", "LOW_FATIGUE",
        "RECOVERING_MARKET_SIGNAL", "DIVERSITY_CONSTRAINT",
        "EVIDENCE_INSUFFICIENT", "MARKET_STALE", "MARKET_UNAVAILABLE",
        "MARKET_REGIME_CHANGE_SUSPECTED", "INSUFFICIENT_ELIGIBLE_ARMS",
        "PROVIDER_CAPABILITY_UNAVAILABLE", "MANUAL_OVERRIDE", "FATIGUE_COOLDOWN",
        "WEAK_POSTERIOR",
    }

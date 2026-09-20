"""Canonical vocabulary: every enum value below is copied verbatim from the phase specs.

These tests are the freeze: renaming or re-casing a value here is a contract change that
must show up as a failing test before any service code sees it.
"""

from __future__ import annotations

import pytest
from plm_contracts import (
    AllocationType,
    ChannelActivationState,
    CommercialUseEligibility,
    DetectorResult,
    GateDisposition,
    GateOutcome,
    GateOverrideStatus,
    ObservationWindow,
    PolicyAction,
    ProvenanceStatus,
    QualityStatus,
    ReviewResult,
    SourceFreshness,
    from_exploration_level,
    to_exploration_level,
)
from pydantic import ValidationError


def _values(enum_type: type) -> set[str]:
    return {member.value for member in enum_type}


class TestVerbatimValues:
    def test_gate_disposition(self) -> None:
        # P5 §9 / §51: QUARANTINED and REJECTED stay distinct (§9).
        assert _values(GateDisposition) == {
            "PASSED",
            "PASSED_WITH_WARNINGS",
            "QUARANTINED",
            "REJECTED",
        }

    def test_gate_override_status_is_not_a_fifth_disposition(self) -> None:
        # P5 §125.
        assert _values(GateOverrideStatus) == {"CLEARED_BY_OVERRIDE"}
        assert _values(GateOverrideStatus).isdisjoint(_values(GateDisposition))

    def test_detector_result(self) -> None:
        # P5 §101: "These are not equivalent."
        assert _values(DetectorResult) == {
            "PASS",
            "WARN",
            "FAIL",
            "UNKNOWN",
            "NOT_APPLICABLE",
            "ERROR",
        }

    def test_policy_action(self) -> None:
        # P5 §44.
        assert _values(PolicyAction) == {
            "PASS",
            "WARN",
            "QUARANTINE",
            "REJECT",
            "REQUEST_RECHECK",
        }

    def test_review_result(self) -> None:
        # P5 §73.
        assert _values(ReviewResult) == {
            "APPROVE",
            "REJECT",
            "REQUEST_REFINEMENT",
            "REQUEST_MORE_EVIDENCE",
            "ESCALATE",
        }

    def test_commercial_use_eligibility(self) -> None:
        # P5 §34: "Never collapse them to Boolean."
        assert _values(CommercialUseEligibility) == {
            "eligible",
            "ineligible",
            "unknown",
            "conditional",
            "expired_or_unverified",
        }

    def test_provenance_status(self) -> None:
        # P5 §37.
        assert _values(ProvenanceStatus) == {
            "COMPLETE",
            "SUFFICIENT",
            "PARTIAL",
            "MISSING",
            "CONFLICTING",
        }

    def test_quality_status(self) -> None:
        # P9 §57.
        assert _values(QualityStatus) == {
            "VALID",
            "PARTIAL",
            "LATE",
            "ESTIMATED",
            "REVISED",
            "ANOMALOUS",
            "QUARANTINED",
            "INVALID",
        }

    def test_source_freshness(self) -> None:
        # P9 §60.
        assert _values(SourceFreshness) == {"FRESH", "ACCEPTABLE", "STALE", "CRITICAL"}

    def test_allocation_type(self) -> None:
        # P2 §34; canonical per ruling R4.
        assert _values(AllocationType) == {"exploit", "adjacent_explore", "radical_explore"}

    def test_channel_activation_state(self) -> None:
        # P2 §117.
        assert _values(ChannelActivationState) == {
            "DEVELOPMENT",
            "DRY_RUN",
            "SHADOW",
            "AUTONOMOUS",
            "SUSPENDED",
        }

    def test_observation_window(self) -> None:
        # P2 §18 / P9.
        assert _values(ObservationWindow) == {"24h", "72h", "7d", "28d", "90d", "lifetime"}


class TestObservationWindowDurations:
    def test_bounded_windows_expose_hours_as_data(self) -> None:
        assert ObservationWindow.H24.duration_hours == 24.0
        assert ObservationWindow.H72.duration_hours == 72.0
        assert ObservationWindow.D7.duration_hours == 168.0
        assert ObservationWindow.D28.duration_hours == 672.0
        assert ObservationWindow.D90.duration_hours == 2160.0

    def test_lifetime_is_unbounded_not_a_large_number(self) -> None:
        assert ObservationWindow.LIFETIME.duration_hours is None
        assert ObservationWindow.LIFETIME.is_unbounded

    def test_nothing_else_is_unbounded(self) -> None:
        assert not any(
            window.is_unbounded
            for window in ObservationWindow
            if window is not ObservationWindow.LIFETIME
        )


class TestAllocationToExplorationLevel:
    """Ruling R4 (C2): P3's `exploration_level` maps from P2's canonical allocation_type."""

    def test_every_allocation_maps_to_its_canonical_token(self) -> None:
        assert to_exploration_level(AllocationType.EXPLOIT) == "exploit"
        assert (
            to_exploration_level(AllocationType.ADJACENT_EXPLORE) == "adjacent_explore"
        )
        assert to_exploration_level(AllocationType.RADICAL_EXPLORE) == "radical_explore"

    def test_mapping_round_trips(self) -> None:
        for allocation in AllocationType:
            assert from_exploration_level(to_exploration_level(allocation)) is allocation

    def test_unknown_tokens_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            from_exploration_level("explore")


class TestGateOutcomeMutex:
    def test_override_accompanies_a_quarantine(self) -> None:
        outcome = GateOutcome(
            disposition=GateDisposition.QUARANTINED,
            override_status=GateOverrideStatus.CLEARED_BY_OVERRIDE,
        )
        assert outcome.disposition is GateDisposition.QUARANTINED
        assert outcome.override_status is GateOverrideStatus.CLEARED_BY_OVERRIDE

    def test_override_accompanies_a_rejection(self) -> None:
        outcome = GateOutcome(
            disposition=GateDisposition.REJECTED,
            override_status=GateOverrideStatus.CLEARED_BY_OVERRIDE,
        )
        assert outcome.override_status is GateOverrideStatus.CLEARED_BY_OVERRIDE

    def test_override_defaults_to_none(self) -> None:
        outcome = GateOutcome(disposition=GateDisposition.PASSED)
        assert outcome.override_status is None

    def test_cleared_by_override_is_rejected_as_a_disposition(self) -> None:
        with pytest.raises(ValidationError):
            GateOutcome(disposition="CLEARED_BY_OVERRIDE")  # type: ignore[arg-type]

    def test_override_cannot_attach_to_a_passed_disposition(self) -> None:
        with pytest.raises(ValidationError):
            GateOutcome(
                disposition=GateDisposition.PASSED_WITH_WARNINGS,
                override_status=GateOverrideStatus.CLEARED_BY_OVERRIDE,
            )

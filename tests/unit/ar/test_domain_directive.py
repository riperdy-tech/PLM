# tests/unit/ar/test_domain_directive.py
from datetime import UTC, datetime

import pytest
from plm_contracts import AllocationType

from services.autonomous_ar.domain.decision import ArmSample, ConstraintRejection
from services.autonomous_ar.domain.directive import DirectiveBrief, ProductionDirective
from services.autonomous_ar.domain.enums import ReasonCode


def _directive(**overrides: object) -> ProductionDirective:
    base: dict[str, object] = {
        "directive_id": "d-1", "decision_id": "dec-1", "arm_id": "arm-1",
        "allocation_type": AllocationType.EXPLOIT, "priority": 1, "quantity": 1,
        "brief": DirectiveBrief(genre="rnb", mood=("dreamy",), use_case=("late_night_drive",),
                                language="english", vocal_mode="vocal",
                                duration_bucket="45_75m", visual_world="rainy_asian_city"),
        "expected_value": 0.72, "uncertainty": 0.31, "budget_limit": 1.0,
        "market_context": {"p1_score": 82.0, "p1_confidence": 0.7},
        "selection_reason": (ReasonCode.STRONG_POSTERIOR.value,),
        "directive_version": "p2-directive-v1", "policy_version": "p2-v1",
        "expires_at": datetime(2026, 9, 27, 6, 0, tzinfo=UTC),
        "exploration_fraction": 0.70, "value_model": "early-stage-proxy-v1",
        "outcome_horizon": "28d",
    }
    base.update(overrides)
    return ProductionDirective(**base)  # type: ignore[arg-type]


def test_directive_carries_the_seventeen_frozen_fields_and_forbids_extras() -> None:
    _directive()  # builds without error; the assertions below pin the frozen shape
    assert set(ProductionDirective.model_fields) == {
        "directive_id", "decision_id", "arm_id", "allocation_type", "priority", "quantity",
        "brief", "expected_value", "uncertainty", "budget_limit", "market_context",
        "selection_reason", "directive_version", "policy_version", "expires_at",
        "exploration_fraction", "value_model", "outcome_horizon",
    }
    assert "status" not in ProductionDirective.model_fields, "status is persistence-only (§52)"
    with pytest.raises(ValueError):
        _directive(quantity=0)
    with pytest.raises(ValueError):
        _directive(naive="field")


def test_brief_is_a_search_region_with_exactly_the_registry_keys() -> None:
    brief = _directive().brief
    assert set(brief.model_dump()) == {
        "genre", "mood", "use_case", "language", "vocal_mode", "duration_bucket",
        "visual_world",
    }


def test_rejections_and_samples_are_plain_records() -> None:
    sample = ArmSample(arm_id="a", sampled_reward=0.71, posterior_expected_reward=0.66)
    rejection = ConstraintRejection(arm_id="a", reason=ReasonCode.DIVERSITY_CONSTRAINT,
                                    detail="genre cap 0.6")
    assert sample.sampled_reward == 0.71 and rejection.reason is ReasonCode.DIVERSITY_CONSTRAINT


def test_expected_value_has_no_upper_bound_because_the_scale_is_the_value_models() -> None:
    """Controller review finding, 2026-09-20 - pins a deliberate *absence* of a constraint.

    The bound belongs to the value model's normalisation, not to the C2 wire. While
    `value_model` is the normalised early-stage proxy the range happens to be 0-1, but P9's
    full economic terms (revenue, subscriber value, catalog value, ...) rescale the field. A
    frozen `le=1.0` would convert that expected change into a change to a frozen seam, so it
    must not exist. The lower bound stays: a negative expected value is meaningless.
    """
    assert _directive(expected_value=12.5).expected_value == 12.5
    assert _directive(expected_value=0.0).expected_value == 0.0
    with pytest.raises(ValueError):
        _directive(expected_value=-0.01)

"""Tests for P3 Measurement Contract and the predeclared outcome horizon (P3 §39–§43, ADR-0031)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import ObservationWindow
from plm_contracts.seams.c7 import ObservationMaturity
from pydantic import ValidationError

from services.experiments.domain.measurement import (
    MaturityRule,
    MeasurementPlan,
    MetricDefinition,
    build_default_measurement_plan,
)

PUBLISHED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _primary() -> MetricDefinition:
    return MetricDefinition(metric_name="retention_30s", is_primary=True)


def test_metric_definition_and_plan() -> None:
    guardrail = MetricDefinition(
        metric_name="skip_rate_5s", higher_is_better=False, is_primary=False
    )
    plan = MeasurementPlan(
        experiment_id="exp_meas_01",
        primary_metric=_primary(),
        outcome_horizon=ObservationWindow.D7,
        maturity_rule=MaturityRule.PUBLICATION_PLUS_HORIZON,
        guardrail_metrics=(guardrail,),
        attribution_windows=(ObservationWindow.H24, ObservationWindow.D7),
        min_sample_size_per_treatment=50,
    )
    assert plan.primary_metric.metric_name == "retention_30s"
    assert plan.outcome_horizon is ObservationWindow.D7
    assert plan.maturity_rule is MaturityRule.PUBLICATION_PLUS_HORIZON


def test_default_plan_requires_and_records_the_horizon() -> None:
    with pytest.raises(TypeError):
        build_default_measurement_plan("exp_meas_02")  # type: ignore[call-arg]
    plan = build_default_measurement_plan(
        "exp_meas_02", outcome_horizon=ObservationWindow.D90, target_metric="completion_rate"
    )
    assert plan.primary_metric.metric_name == "completion_rate"
    assert plan.outcome_horizon is ObservationWindow.D90
    assert ObservationWindow.D90 in plan.attribution_windows
    assert len(plan.guardrail_metrics) >= 1


def test_plan_without_horizon_or_rule_is_refused() -> None:
    with pytest.raises(ValidationError):
        MeasurementPlan(  # type: ignore[call-arg]
            experiment_id="exp_x",
            primary_metric=_primary(),
            maturity_rule=MaturityRule.PUBLICATION_PLUS_HORIZON,
            attribution_windows=(ObservationWindow.D28,),
        )
    with pytest.raises(ValidationError):
        MeasurementPlan(  # type: ignore[call-arg]
            experiment_id="exp_x",
            primary_metric=_primary(),
            outcome_horizon=ObservationWindow.D28,
            attribution_windows=(ObservationWindow.D28,),
        )


def test_horizon_must_be_one_of_the_attribution_windows() -> None:
    with pytest.raises(ValidationError, match="attribution_windows"):
        MeasurementPlan(
            experiment_id="exp_x",
            primary_metric=_primary(),
            outcome_horizon=ObservationWindow.D28,
            maturity_rule=MaturityRule.PUBLICATION_PLUS_HORIZON,
            attribution_windows=(ObservationWindow.H24, ObservationWindow.D7),
        )


def test_maturity_is_derived_from_publication_plus_horizon() -> None:
    plan = build_default_measurement_plan("exp_m", outcome_horizon=ObservationWindow.D28)
    assert plan.matures_at(PUBLISHED) == PUBLISHED + timedelta(days=28)
    early = plan.maturity(published_at=PUBLISHED, as_of=PUBLISHED + timedelta(days=27))
    late = plan.maturity(published_at=PUBLISHED, as_of=PUBLISHED + timedelta(days=28))
    assert early is ObservationMaturity.PROVISIONAL
    assert late is ObservationMaturity.MATURE


def test_lifetime_horizon_never_matures_until_closed() -> None:
    plan = build_default_measurement_plan("exp_l", outcome_horizon=ObservationWindow.LIFETIME)
    far = PUBLISHED + timedelta(days=3650)
    assert plan.matures_at(PUBLISHED) is None
    assert plan.maturity(published_at=PUBLISHED, as_of=far) is ObservationMaturity.PROVISIONAL
    assert (
        plan.maturity(published_at=PUBLISHED, as_of=far, closed_by_source=True)
        is ObservationMaturity.CLOSED
    )

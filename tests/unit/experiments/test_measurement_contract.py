"""Tests for P3 Measurement Contract and Metric Registry (Task P3.9)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.experiments.domain.measurement import (
    AttributionWindow,
    MeasurementPlan,
    MetricDefinition,
    build_default_measurement_plan,
)


def test_metric_definition_and_plan() -> None:
    primary = MetricDefinition(
        metric_name="retention_30s",
        description="Fraction of listeners who listen past 30 seconds",
        unit="ratio",
        higher_is_better=True,
        is_primary=True,
    )
    guardrail = MetricDefinition(
        metric_name="skip_rate_5s",
        description="Fraction of listeners who skip within 5 seconds",
        unit="ratio",
        higher_is_better=False,
        is_primary=False,
    )
    plan = MeasurementPlan(
        experiment_id="exp_meas_01",
        primary_metric=primary,
        secondary_metrics=(),
        guardrail_metrics=(guardrail,),
        attribution_windows=(AttributionWindow(hours=24), AttributionWindow(hours=168)),
        min_sample_size_per_treatment=50,
    )

    assert plan.experiment_id == "exp_meas_01"
    assert plan.primary_metric.metric_name == "retention_30s"
    assert len(plan.attribution_windows) == 2
    assert plan.attribution_windows[1].hours == 168


def test_default_measurement_plan_builder() -> None:
    plan = build_default_measurement_plan("exp_meas_02", target_metric="completion_rate")
    assert plan.primary_metric.metric_name == "completion_rate"
    assert plan.primary_metric.higher_is_better
    assert len(plan.guardrail_metrics) >= 1
    assert len(plan.attribution_windows) >= 2


def test_attribution_window_validation() -> None:
    with pytest.raises(ValidationError):
        AttributionWindow(hours=0)

    with pytest.raises(ValidationError):
        AttributionWindow(hours=-10)

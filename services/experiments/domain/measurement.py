"""Measurement contracts, metric definitions, and attribution windows (PHASE-P3 §39–§43)."""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

STANDARD_ATTRIBUTION_HOURS: Final[tuple[int, ...]] = (24, 72, 168, 672)  # 24h, 3d, 7d, 28d


class AttributionWindow(BaseModel):
    """Observation period over which causal experiment metrics are accumulated (§42)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hours: int = Field(ge=1)


class MetricDefinition(BaseModel):
    """Specification of an evaluated experiment metric (§40, §41)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str
    description: str = ""
    unit: str = "ratio"
    higher_is_better: bool = True
    is_primary: bool = False


class MeasurementPlan(BaseModel):
    """Formal measurement and evaluation plan for an experiment (§39)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str
    primary_metric: MetricDefinition
    secondary_metrics: tuple[MetricDefinition, ...] = ()
    guardrail_metrics: tuple[MetricDefinition, ...] = ()
    attribution_windows: tuple[AttributionWindow, ...] = ()
    min_sample_size_per_treatment: int = Field(default=100, ge=1)


def build_default_measurement_plan(
    experiment_id: str,
    target_metric: str = "retention_30s",
) -> MeasurementPlan:
    """Build a standard measurement plan with primary metric and safety guardrails."""
    primary = MetricDefinition(
        metric_name=target_metric,
        description=f"Primary optimization metric: {target_metric}",
        unit="ratio",
        higher_is_better=True,
        is_primary=True,
    )
    guardrail = MetricDefinition(
        metric_name="skip_rate_5s",
        description="Early skip rate within first 5 seconds",
        unit="ratio",
        higher_is_better=False,
        is_primary=False,
    )
    secondary = MetricDefinition(
        metric_name="save_rate",
        description="Fraction of users saving track to playlist/library",
        unit="ratio",
        higher_is_better=True,
        is_primary=False,
    )

    windows = tuple(AttributionWindow(hours=h) for h in STANDARD_ATTRIBUTION_HOURS)

    return MeasurementPlan(
        experiment_id=experiment_id,
        primary_metric=primary,
        secondary_metrics=(secondary,),
        guardrail_metrics=(guardrail,),
        attribution_windows=windows,
        min_sample_size_per_treatment=50,
    )

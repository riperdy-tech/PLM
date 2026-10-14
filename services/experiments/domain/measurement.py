"""Measurement contracts, metric definitions and the predeclared outcome horizon (P3 §39–§43).

ADR-0031: P2 declares the outcome horizon on the C2 directive and P3 records it in the
experiment's measurement plan at design time, before any content exists, so the horizon
cannot be chosen after the results are seen. Maturity is never asserted: an outcome is
mature once ``published_at + outcome_horizon`` has passed (``lifetime`` has no end and
matures only when its source closes it).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from plm_contracts import ObservationWindow
from plm_contracts.measurement import maturity_at, window_end
from plm_contracts.seams.c7 import ObservationMaturity
from pydantic import BaseModel, ConfigDict, Field, model_validator

STANDARD_ATTRIBUTION_WINDOWS: Final[tuple[ObservationWindow, ...]] = (
    ObservationWindow.H24,
    ObservationWindow.H72,
    ObservationWindow.D7,
    ObservationWindow.D28,
    ObservationWindow.LIFETIME,
)
"""§42: windows P9 reports per content. The horizon is one of them; the rest are recorded
for interpretation and never move P2's posterior (ADR-0031)."""


class MaturityRule(StrEnum):
    """How an outcome's maturity is derived. Stated as data on the plan."""

    PUBLICATION_PLUS_HORIZON = "publication_plus_horizon"


class MetricDefinition(BaseModel):
    """Specification of an evaluated experiment metric (§40, §41)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(min_length=1)
    description: str = ""
    unit: str = "ratio"
    higher_is_better: bool = True
    is_primary: bool = False


class MeasurementPlan(BaseModel):
    """Formal, predeclared measurement plan for an experiment (§39, ADR-0031)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str = Field(min_length=1)
    primary_metric: MetricDefinition
    outcome_horizon: ObservationWindow
    """The window whose mature outcome is the experiment's one effective outcome."""
    maturity_rule: MaturityRule
    secondary_metrics: tuple[MetricDefinition, ...] = ()
    guardrail_metrics: tuple[MetricDefinition, ...] = ()
    attribution_windows: tuple[ObservationWindow, ...]
    min_sample_size_per_treatment: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def _horizon_is_an_attribution_window(self) -> MeasurementPlan:
        if self.outcome_horizon not in self.attribution_windows:
            raise ValueError(
                f"outcome_horizon {self.outcome_horizon.value!r} must be one of the plan's "
                "attribution_windows"
            )
        if len(set(self.attribution_windows)) != len(self.attribution_windows):
            raise ValueError("attribution_windows must not repeat a window")
        return self

    def matures_at(self, published_at: datetime) -> datetime | None:
        """When the outcome at the horizon becomes mature; ``None`` for ``lifetime``."""
        return window_end(published_at, self.outcome_horizon)

    def maturity(
        self, *, published_at: datetime, as_of: datetime, closed_by_source: bool = False
    ) -> ObservationMaturity:
        """Maturity of the horizon outcome at ``as_of``, derived per ``maturity_rule``."""
        return maturity_at(
            published_at=published_at,
            window=self.outcome_horizon,
            as_of=as_of,
            closed_by_source=closed_by_source,
        )


def build_default_measurement_plan(
    experiment_id: str,
    *,
    outcome_horizon: ObservationWindow,
    target_metric: str = "retention_30s",
) -> MeasurementPlan:
    """A plan with the given primary metric, standard guardrails and the declared horizon.

    ``outcome_horizon`` has no default: it comes from the P2 directive (ADR-0031).
    """
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
    windows = STANDARD_ATTRIBUTION_WINDOWS
    if outcome_horizon not in windows:
        windows = (*windows, outcome_horizon)

    return MeasurementPlan(
        experiment_id=experiment_id,
        primary_metric=primary,
        outcome_horizon=outcome_horizon,
        maturity_rule=MaturityRule.PUBLICATION_PLUS_HORIZON,
        secondary_metrics=(secondary,),
        guardrail_metrics=(guardrail,),
        attribution_windows=windows,
        min_sample_size_per_treatment=50,
    )

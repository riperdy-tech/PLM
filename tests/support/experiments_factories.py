"""Design facts every hand-built P3 ``Experiment`` must carry (ADR-0031, stage R2).

Tests that construct an ``Experiment`` directly (instead of through ``design_experiment``)
spread ``design_facts(experiment_id)`` into the constructor. The values are explicit test
inputs, not defaults the runtime would ever fill in.
"""

from __future__ import annotations

from typing import Any

from plm_contracts import ObservationWindow

from services.experiments.domain.experiment import RandomizationRecord
from services.experiments.domain.measurement import build_default_measurement_plan

__all__ = ["design_facts"]


def design_facts(
    experiment_id: str,
    *,
    budget_limit: float = 100.0,
    outcome_horizon: ObservationWindow = ObservationWindow.D28,
    target_metric: str = "retention_30s",
) -> dict[str, Any]:
    return {
        "measurement_plan": build_default_measurement_plan(
            experiment_id, outcome_horizon=outcome_horizon, target_metric=target_metric
        ),
        "budget_limit": budget_limit,
        "randomization": RandomizationRecord(seed=0, algorithm="test-fixture", version="test"),
    }

"""Design facts every hand-built P3 ``Experiment`` must carry (ADR-0031, stage R2).

Tests that construct an ``Experiment`` directly (instead of through ``design_experiment``)
spread ``design_facts(experiment_id)`` into the constructor. The values are explicit test
inputs, not defaults the runtime would ever fill in.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from plm_contracts import ObservationWindow
from plm_contracts.seams.c3 import DurationRangeSeconds

from services.experiments.domain.experiment import RandomizationRecord
from services.experiments.domain.measurement import build_default_measurement_plan

__all__ = ["design_facts", "playlist_spec_fields"]


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


def playlist_spec_fields(
    *,
    playlist_spec_id: str = "ply_01",
    experiment_id: str = "exp_01",
    brand_id: str = "brand_lofi",
    min_seconds: int = 5400,
    max_seconds: int = 7200,
    planned_track_duration_seconds: int = 180,
    base_track_count: int = 30,
    spare_track_count: int = 8,
    max_extension_track_count: int = 19,
    sequencing_profile: str = "progressive_arc",
) -> dict[str, Any]:
    """Explicit playlist-spec facts for hand-built packages (90–120 min, 3-min tracks)."""
    return {
        "playlist_spec_id": playlist_spec_id,
        "experiment_id": experiment_id,
        "brand_id": brand_id,
        "duration_range_seconds": DurationRangeSeconds(
            min_seconds=min_seconds, max_seconds=max_seconds
        ),
        "target_duration_seconds": round((min_seconds + max_seconds) / 2),
        "planned_track_duration_seconds": planned_track_duration_seconds,
        "base_track_count": base_track_count,
        "spare_track_count": spare_track_count,
        "track_count_target": base_track_count + spare_track_count,
        "max_extension_track_count": max_extension_track_count,
        "spare_ratio": Decimal("0.25"),
        "supply_policy_version": "test-supply",
        "sequencing_profile": sequencing_profile,
    }

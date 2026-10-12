"""P10 feature projection model generator (Seam C8, P9 §155)."""

from __future__ import annotations

from ..domain.models import ExperimentOutcomeRecord, P10FeatureView


def build_p10_feature_view(
    outcome: ExperimentOutcomeRecord,
    *,
    views: int = 0,
    watch_time_hours: float = 0.0,
    subscribers_gained: int = 0,
    spotify_streams: int = 0,
    data_completeness: float = 1.0,
    fidelity: float = 1.0,
    contamination: bool = False,
) -> P10FeatureView:
    """Project an experiment outcome into the Seam C8 P10 feature view."""
    return P10FeatureView(
        unit_id=outcome.unit_id,
        experiment_id=outcome.experiment_id,
        arm_id=outcome.content_arm_id,
        content_genome_id=outcome.content_genome_id,
        observation_window=outcome.observation_window.value,
        maturity=outcome.maturity.upper(),
        primary_outcome={
            "metric": outcome.primary_metric,
            "value": outcome.primary_value,
            "status": "REPORTED",
        },
        secondary_outcomes={
            "views": views,
            "watch_time_hours": round(watch_time_hours, 2),
            "subscribers_gained": subscribers_gained,
            "spotify_streams": spotify_streams,
        },
        quality={
            "data_completeness": round(data_completeness, 2),
            "fidelity": round(fidelity, 2),
            "contamination": contamination,
            "attribution_confidence": outcome.attribution_confidence,
        },
    )

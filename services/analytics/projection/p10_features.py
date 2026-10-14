"""P10 feature projection (Seam C8, P9 §155, ADR-0025).

Projects an experiment outcome into the frozen C8 view. Every secondary metric and
quality assessment is an explicit input. ``None`` means not measured or not assessed and
is served as ``null``, never as a zero, a ``1.0`` or a ``false``.
"""

from __future__ import annotations

from ..domain.models import ExperimentOutcomeRecord, P10FeatureView

__all__ = ["build_p10_feature_view"]


def build_p10_feature_view(
    outcome: ExperimentOutcomeRecord,
    *,
    views: float | None,
    watch_time_hours: float | None,
    subscribers_gained: float | None,
    spotify_streams: float | None,
    data_completeness: float | None,
    fidelity: float | None,
    contamination: bool | None,
) -> P10FeatureView:
    """Project an experiment outcome into the Seam C8 P10 feature view."""
    return P10FeatureView.build(
        unit_id=outcome.unit_id,
        experiment_id=outcome.experiment_id,
        arm_id=outcome.content_arm_id,
        content_genome_id=outcome.content_genome_id,
        observation_window=outcome.observation_window,
        maturity=outcome.maturity,
        primary_outcome={
            "metric": outcome.primary_metric,
            "value": outcome.primary_value,
            "status": outcome.maturity,
        },
        secondary_outcomes={
            "views": views,
            "watch_time_hours": round(watch_time_hours, 2) if watch_time_hours is not None else None,
            "subscribers_gained": subscribers_gained,
            "spotify_streams": spotify_streams,
        },
        quality={
            "data_completeness": data_completeness,
            "fidelity": fidelity,
            "contamination": contamination,
            "attribution_confidence": outcome.attribution_confidence,
        },
    )

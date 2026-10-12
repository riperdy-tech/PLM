"""Domain package for Phase P9 Analytics & Economics Engine."""

from .enums import (
    CostCategory,
    DataFreshness,
    MetricSource,
    MetricStatus,
    ObservationWindow,
    PaybackStatus,
    RevenueStatus,
)
from .ids import (
    build_cost_id,
    build_observation_id,
    build_outcome_id,
    build_statement_id,
    compute_sha256,
)
from .models import (
    CostItem,
    ExperimentOutcomeRecord,
    NormalizedRewardPayload,
    P10FeatureView,
    RevenueStatement,
    TrackMetricObservation,
    UnitEconomics,
    VideoMetricObservation,
)

__all__ = [
    "CostCategory",
    "CostItem",
    "DataFreshness",
    "ExperimentOutcomeRecord",
    "MetricSource",
    "MetricStatus",
    "NormalizedRewardPayload",
    "ObservationWindow",
    "P10FeatureView",
    "PaybackStatus",
    "RevenueStatement",
    "RevenueStatus",
    "TrackMetricObservation",
    "UnitEconomics",
    "VideoMetricObservation",
    "build_cost_id",
    "build_observation_id",
    "build_outcome_id",
    "build_statement_id",
    "compute_sha256",
]

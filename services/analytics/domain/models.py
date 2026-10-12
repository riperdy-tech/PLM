"""Domain models for Phase P9 Analytics & Economics Engine (P9 §14, §15, §16, §71, Ruling R10, R11)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .enums import (
    CostCategory,
    MetricSource,
    MetricStatus,
    ObservationWindow,
    PaybackStatus,
    RevenueStatus,
)


class VideoMetricObservation(BaseModel):
    """Normalized observation for YouTube video performance."""

    model_config = ConfigDict(frozen=True)

    id: str
    video_id: str
    channel_id: str
    window: ObservationWindow
    date_str: str  # YYYY-MM-DD
    views: int = 0
    watch_time_minutes: float = 0.0
    average_view_duration_seconds: float = 0.0
    average_percentage_viewed: float = 0.0
    subscribers_gained: int = 0
    subscribers_lost: int = 0
    estimated_revenue_usd: float = 0.0
    revenue_status: RevenueStatus = RevenueStatus.ESTIMATED
    source: MetricSource = MetricSource.YOUTUBE_ANALYTICS
    status: MetricStatus = MetricStatus.VALID
    observed_at: datetime


class TrackMetricObservation(BaseModel):
    """Normalized observation for distributed track performance."""

    model_config = ConfigDict(frozen=True)

    id: str
    isrc: str
    track_id: str
    release_id: str
    window: ObservationWindow
    date_str: str  # YYYY-MM-DD
    streams: int = 0
    reported_revenue_usd: float = 0.0
    provider: str
    revenue_status: RevenueStatus = RevenueStatus.REPORTED
    source: MetricSource = MetricSource.DISTRIBUTOR_STATEMENT
    status: MetricStatus = MetricStatus.VALID
    observed_at: datetime


class RevenueStatement(BaseModel):
    """Deduplicated distributor revenue statement (Ruling R10, P9 §53, §54)."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider: str
    period: str
    total_revenue_usd: float
    total_streams: int
    file_hash: str
    source_reference: str
    received_at: datetime


class CostItem(BaseModel):
    """Cost ledger entry allocated to production, compute, or distribution."""

    model_config = ConfigDict(frozen=True)

    id: str
    entity_id: str
    entity_type: str  # video, track, release, arm
    category: CostCategory
    amount_usd: float
    period: str
    description: str
    incurred_at: datetime


class UnitEconomics(BaseModel):
    """P&L and unit economic calculation for an entity."""

    model_config = ConfigDict(frozen=True)

    entity_id: str
    entity_type: str
    gross_revenue_usd: float
    allocated_cost_usd: float
    net_contribution_usd: float
    profit_margin: float
    roi: float
    rpm_usd: float | None = None
    revenue_per_stream_usd: float | None = None
    payback_days: int | None = None
    payback_status: PaybackStatus = PaybackStatus.NOT_REPAID
    as_of: datetime


class ExperimentOutcomeRecord(BaseModel):
    """Canonical experiment measurement outcome record."""

    model_config = ConfigDict(frozen=True)

    id: str
    experiment_id: str
    experiment_family_id: str
    content_arm_id: str
    content_genome_id: str
    unit_id: str
    observation_window: ObservationWindow
    primary_metric: str = "net_contribution"
    primary_value: float
    normalized_reward: float  # [0.0, 1.0]
    reward_version: str = "v1"
    attribution_confidence: float = 1.0
    maturity: str = "MATURE"
    quality_status: str = "ELIGIBLE"
    created_at: datetime


class NormalizedRewardPayload(BaseModel):
    """Seam C7 Leg 2 contract payload: P9 → P2 (POST /ar/results) (Ruling R11, P2 §72)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str
    arm_id: str
    content_id: str
    observation_window: str
    status: str
    observed_at: str  # ISO-8601 string
    raw_metrics: dict[str, Any]
    normalized_reward: float
    reward_version: str
    attribution_confidence: float


class P10FeatureView(BaseModel):
    """Seam C8 contract payload: P9 → P10 (GET /analytics/p10/features) (P9 §155)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str
    experiment_id: str
    arm_id: str
    content_genome_id: str
    observation_window: str
    maturity: str
    primary_outcome: dict[str, Any]
    secondary_outcomes: dict[str, Any]
    quality: dict[str, Any]

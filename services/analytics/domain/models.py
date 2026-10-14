"""Domain models for Phase P9 Analytics & Economics Engine (P9 §14, §15, §16, §71, Ruling R10, R11)."""

from __future__ import annotations

from datetime import datetime

from plm_contracts import QualityStatus
from plm_contracts.seams.c7 import ObservationMaturity, RewardResultPayload
from plm_contracts.seams.c8 import P10FeatureViewPayload
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
    track_id: str | None
    release_id: str | None
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
    unattributed_revenue_usd: float
    """Revenue on rows with no ISRC: reported, but not attributable to a recording."""
    unattributed_row_count: int


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
    reward_version: str
    attribution_confidence: float
    maturity: ObservationMaturity
    quality_status: QualityStatus | None
    """``None`` means P9 did not assess data quality for this outcome."""
    created_at: datetime


NormalizedRewardPayload = RewardResultPayload
"""Seam C7 leg 2 (R11, P2 §72): the shared ``plm_contracts.seams.c7`` model."""


P10FeatureView = P10FeatureViewPayload
"""Seam C8 view (P9 §155): the shared ``plm_contracts.seams.c8`` model."""

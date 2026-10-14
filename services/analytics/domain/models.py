"""Domain models for Phase P9 Analytics & Economics Engine (P9 §14–§23, §71, R10, R11).

Money is `plm_contracts.money.Money` (Decimal + ISO 4217 currency, ADR-0031); no amount
carries an implied currency. Every metric record declares its
`plm_contracts.measurement.ObservationKind`: YouTube video metrics are cumulative
snapshots per window, distributor statement lines and costs are incremental amounts per
period. A metric that was not measured is ``None``, never ``0``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from plm_contracts import QualityStatus
from plm_contracts.measurement import ObservationKind
from plm_contracts.money import Money
from plm_contracts.seams.c7 import ObservationMaturity, RewardResultPayload
from plm_contracts.seams.c8 import P10FeatureViewPayload
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .enums import (
    CostCategory,
    MetricSource,
    MetricStatus,
    ObservationWindow,
    PaybackStatus,
    RevenueStatus,
)

FX_METHOD_TRANSACTION_DATE = "transaction_date"
"""P9 §110: each amount converts at the latest recorded rate at or before its own
instant (observation, statement receipt, or cost incurrence). Never mixed silently."""


class VideoMetricObservation(BaseModel):
    """One YouTube snapshot of a video for one window (cumulative since publication).

    ``None`` means the metric was not measured; it is stored as NULL and never summed or
    defaulted to zero. ``estimated_revenue`` is a platform estimate (P9 §111) and is
    never booked as realized revenue.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    video_id: str
    channel_id: str
    window: ObservationWindow
    date_str: str  # YYYY-MM-DD
    observation_kind: ObservationKind = ObservationKind.CUMULATIVE_SNAPSHOT
    views: int | None
    watch_time_minutes: float | None
    average_view_duration_seconds: float | None
    average_percentage_viewed: float | None
    subscribers_gained: int | None
    subscribers_lost: int | None
    estimated_revenue: Money | None
    """``None`` when not measured, or when a legacy row has no declared currency."""
    estimated_revenue_unreconciled: Decimal | None = None
    """An amount stored before ADR-0031 with no declared currency: excluded from every
    money aggregation and reported as unreconciled, never assumed to be USD."""
    revenue_status: RevenueStatus = RevenueStatus.ESTIMATED
    source: MetricSource = MetricSource.YOUTUBE_ANALYTICS
    status: MetricStatus = MetricStatus.VALID
    observed_at: datetime


class TrackMetricObservation(BaseModel):
    """One distributor statement line for a recording: an incremental amount per period.

    ``window`` is ``None`` for statement lines: they cover a period (``date_str``), not a
    window since publication.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    isrc: str
    track_id: str | None
    release_id: str | None
    window: ObservationWindow | None
    date_str: str  # statement period
    observation_kind: ObservationKind = ObservationKind.INCREMENTAL
    streams: int | None
    reported_revenue: Money | None
    """``None`` only for a legacy row with no declared currency (see below)."""
    reported_revenue_unreconciled: Decimal | None = None
    """A pre-ADR-0031 amount with no declared currency: excluded and reported."""
    provider: str
    revenue_status: RevenueStatus = RevenueStatus.REPORTED
    source: MetricSource = MetricSource.DISTRIBUTOR_STATEMENT
    status: MetricStatus = MetricStatus.VALID
    observed_at: datetime


class RevenueStatement(BaseModel):
    """Deduplicated distributor revenue statement (Ruling R10, P9 §53, §54).

    ``currency`` is the statement currency declared at ingestion; ``None`` only for
    statements stored before ADR-0031, whose amounts are then unreconciled.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    provider: str
    period: str
    currency: str | None
    total_revenue_amount: Decimal
    total_streams: int
    """Sum of the streams the statement reported; lines without a stream count add 0."""
    file_hash: str
    source_reference: str
    received_at: datetime
    unattributed_revenue_amount: Decimal
    """Revenue on rows with no ISRC: reported, but not attributable to a recording."""
    unattributed_row_count: int

    @property
    def total_revenue(self) -> Money | None:
        if self.currency is None:
            return None
        return Money.of(self.total_revenue_amount, self.currency)

    @property
    def unattributed_revenue(self) -> Money | None:
        if self.currency is None:
            return None
        return Money.of(self.unattributed_revenue_amount, self.currency)


class CostItem(BaseModel):
    """Cost ledger entry: an incremental amount for one (entity, category, period)."""

    model_config = ConfigDict(frozen=True)

    id: str
    entity_id: str
    entity_type: str  # video, track, release, arm
    category: CostCategory
    amount: Money | None
    """``None`` only for a legacy row with no declared currency (see below)."""
    amount_unreconciled: Decimal | None = None
    """A pre-ADR-0031 amount with no declared currency: excluded and reported."""
    period: str
    description: str
    incurred_at: datetime


class FxProvenance(BaseModel):
    """How one source amount reached the reporting currency (P9 §23)."""

    model_config = ConfigDict(frozen=True)

    record_id: str
    original: Money
    converted: Money
    rate_id: str | None
    """``None`` when the amount was already in the reporting currency."""
    inverted: bool


class UnitEconomics(BaseModel):
    """Unit economics of one entity in the reporting currency (P9 §19–§23, §96, ADR-0031).

    * ``estimated_revenue`` is the latest YouTube platform estimate for ``window``
      (``estimated_revenue_status = estimated``). It is shown, never booked: it does not
      enter ``net_contribution``.
    * ``realized_revenue`` is the sum of reported/final statement lines (incremental per
      period). ``None`` when no statement line exists for the entity: not reported is
      not zero.
    * ``net_contribution = realized_revenue - allocated_cost`` only when both are known
      and nothing relevant is unreconciled; otherwise ``None`` with
      ``net_contribution_blocked_reason``.
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str
    entity_type: str
    reporting_currency: str
    window: ObservationWindow
    estimated_revenue: Money | None
    estimated_revenue_status: RevenueStatus = RevenueStatus.ESTIMATED
    realized_revenue: Money | None
    allocated_cost: Money | None
    net_contribution: Money | None
    net_contribution_blocked_reason: str | None = None
    profit_margin: Decimal | None
    """``net / realized``; ``None`` when undefined (no net, or zero realized revenue)."""
    roi: Decimal | None
    """``net / cost``; ``None`` when undefined (no net, or zero cost)."""
    views: int | None = None
    streams: int | None = None
    estimated_rpm: Money | None = None
    """Estimated revenue per 1,000 views of the same snapshot window (an estimate)."""
    realized_revenue_per_stream: Money | None = None
    payback_days: int | None = None
    payback_status: PaybackStatus | None = None
    """``None`` when cost or realized revenue is unknown."""
    fx_method: str = FX_METHOD_TRANSACTION_DATE
    fx_rate_ids: list[str] = Field(default_factory=list)
    conversions: list[FxProvenance] = Field(default_factory=list)
    unreconciled_record_ids: list[str] = Field(default_factory=list)
    """Records with no declared currency, excluded from every amount above."""
    as_of: AwareDatetime


class ExperimentOutcomeRecord(BaseModel):
    """Canonical experiment measurement outcome record.

    ``normalized_reward`` is the ``engagement-contribution-proxy-v1`` score: a bounded
    proxy of net contribution and engagement, not expected economic value (ADR-0031).
    ``primary_value`` is the net contribution in ``primary_value_currency`` (the P9
    reporting currency at evaluation time; ``None`` only for pre-ADR-0031 rows).
    """

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
    primary_value_currency: str | None
    normalized_reward: float  # [0.0, 1.0]; a proxy, not expected value
    reward_version: str
    attribution_confidence: float
    """Passed through to P2 unchanged; never folded into ``normalized_reward``."""
    maturity: ObservationMaturity
    """Derived from ``published_at`` and the window at observation time, never asserted."""
    published_at: datetime | None
    """``None`` only for pre-ADR-0031 rows, whose maturity was caller-asserted."""
    quality_status: QualityStatus | None
    """``None`` means P9 did not assess data quality for this outcome."""
    created_at: datetime


NormalizedRewardPayload = RewardResultPayload
"""Seam C7 leg 2 (R11, P2 §72): the shared ``plm_contracts.seams.c7`` model."""


P10FeatureView = P10FeatureViewPayload
"""Seam C8 view (P9 §155): the shared ``plm_contracts.seams.c8`` model."""

"""SQLAlchemy ORM models for Phase P9 Analytics & Economics Engine (P9 §14, §15, §16, §53, §54, Ruling R10, R11).

Money columns are an exact ``Numeric(20, 6)`` amount plus a ``String(3)`` currency
(ADR-0031). A NULL currency exists only on rows stored before revision
``0022_analytics_money``: currency unknown, so the amount is excluded from every money
aggregation and reported as unreconciled, never assumed to be USD.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


ANALYTICS_METADATA = Base.metadata

AMOUNT = sa.Numeric(20, 6, asdecimal=True)
"""Ledger precision: six decimal places (``plm_contracts.money.LEDGER_QUANTUM``)."""
FX_RATE = sa.Numeric(24, 12, asdecimal=True)
CURRENCY = sa.String(3)


class VideoMetricRow(Base):
    """One YouTube snapshot per (video, window, date). NULL metric = not measured."""

    __tablename__ = "video_metrics"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    window: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    date_str: Mapped[str] = mapped_column(sa.String(10), nullable=False, index=True)
    observation_kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    views: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    watch_time_minutes: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    average_view_duration_seconds: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    average_percentage_viewed: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    subscribers_gained: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    subscribers_lost: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    estimated_revenue_amount: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    estimated_revenue_currency: Mapped[str | None] = mapped_column(CURRENCY, nullable=True)
    revenue_status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="estimated")
    source: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="youtube_analytics")
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="valid")
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class TrackMetricRow(Base):
    """One distributor statement line per (ISRC, period): an incremental amount."""

    __tablename__ = "track_metrics"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    isrc: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    # NULL = the statement row named no track/release and the ISRC is not linked
    # to a release (ADR-0030); never an invented id.
    track_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    release_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    # NULL for incremental statement lines: they cover a period, not a window.
    window: Mapped[str | None] = mapped_column(sa.String(32), nullable=True, index=True)
    date_str: Mapped[str] = mapped_column(sa.String(10), nullable=False, index=True)
    observation_kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    streams: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    reported_revenue_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    reported_revenue_currency: Mapped[str | None] = mapped_column(CURRENCY, nullable=True)
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    revenue_status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="reported")
    source: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="distributor_statement")
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="valid")
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class RevenueStatementRow(Base):
    """Parsed canonical distributor statements (Ruling R10, P9 §53, §54)."""

    __tablename__ = "revenue_statements"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    period: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    currency: Mapped[str | None] = mapped_column(CURRENCY, nullable=True)
    total_revenue_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    total_streams: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    file_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    source_reference: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    # Rows with no ISRC cannot be attributed to a recording (ADR-0030).
    unattributed_revenue_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    unattributed_row_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)


class CostItemRow(Base):
    __tablename__ = "cost_items"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    entity_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    category: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    currency: Mapped[str | None] = mapped_column(CURRENCY, nullable=True)
    period: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    description: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    incurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class FxRateRow(Base):
    """One recorded conversion rate: 1 ``base`` = ``rate`` ``quote`` (ADR-0031, P9 §23)."""

    __tablename__ = "fx_rates"
    __table_args__ = (
        sa.UniqueConstraint("base", "quote", "as_of", "source", name="uq_fx_rates_pair_instant_source"),
        {"schema": "analytics"},
    )

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    base: Mapped[str] = mapped_column(CURRENCY, nullable=False, index=True)
    quote: Mapped[str] = mapped_column(CURRENCY, nullable=False, index=True)
    rate: Mapped[Decimal] = mapped_column(FX_RATE, nullable=False)
    source: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    as_of: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class ExperimentOutcomeRow(Base):
    __tablename__ = "experiment_outcomes"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    experiment_family_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_arm_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    content_genome_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    unit_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    observation_window: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    primary_metric: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="net_contribution")
    primary_value: Mapped[float] = mapped_column(sa.Float, nullable=False)
    # NULL only on rows stored before ADR-0031 (currency never declared).
    primary_value_currency: Mapped[str | None] = mapped_column(CURRENCY, nullable=True)
    normalized_reward: Mapped[float] = mapped_column(sa.Float, nullable=False)
    reward_version: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="v1")
    attribution_confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=1.0)
    maturity: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # NULL only on rows stored before ADR-0031, whose maturity was caller-asserted.
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    # NULL = quality not assessed (ADR-0030); no defaulted 'ELIGIBLE'.
    quality_status: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class RewardPostedRow(Base):
    """Feedback reward post audit ledger (Ruling R11, Seam C7 Leg 2)."""

    __tablename__ = "rewards_posted"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    arm_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    content_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    observation_window: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    normalized_reward: Mapped[float] = mapped_column(sa.Float, nullable=False)
    reward_version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    attribution_confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)
    raw_metrics: Mapped[Any] = mapped_column(sa.JSON, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class ReleaseLinkRow(Base):
    """One released recording and the lineage it was released under (C7 leg 1, ADR-0030)."""

    __tablename__ = "release_links"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    release_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    isrc: Mapped[str] = mapped_column(sa.String(32), primary_key=True, index=True)
    media_package_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    lineage_json: Mapped[Any] = mapped_column(sa.JSON, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

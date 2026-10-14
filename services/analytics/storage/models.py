"""SQLAlchemy ORM models for Phase P9 Analytics & Economics Engine (P9 §14, §15, §16, §53, §54, Ruling R10, R11)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


ANALYTICS_METADATA = Base.metadata


class VideoMetricRow(Base):
    __tablename__ = "video_metrics"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    window: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    date_str: Mapped[str] = mapped_column(sa.String(10), nullable=False, index=True)
    views: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    watch_time_minutes: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    average_view_duration_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    average_percentage_viewed: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    subscribers_gained: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    subscribers_lost: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    estimated_revenue_usd: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    revenue_status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="estimated")
    source: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="youtube_analytics")
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="valid")
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class TrackMetricRow(Base):
    __tablename__ = "track_metrics"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    isrc: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    # NULL = the statement row named no track/release and the ISRC is not linked
    # to a release (ADR-0030); never an invented id.
    track_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    release_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    window: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    date_str: Mapped[str] = mapped_column(sa.String(10), nullable=False, index=True)
    streams: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    reported_revenue_usd: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
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
    total_revenue_usd: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    total_streams: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    file_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    source_reference: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    # Rows with no ISRC cannot be attributed to a recording (ADR-0030).
    unattributed_revenue_usd: Mapped[float] = mapped_column(
        sa.Float, nullable=False, default=0.0
    )
    unattributed_row_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)


class CostItemRow(Base):
    __tablename__ = "cost_items"
    __table_args__ = {"schema": "analytics"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    entity_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    category: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    amount_usd: Mapped[float] = mapped_column(sa.Float, nullable=False)
    period: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    description: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    incurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


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
    normalized_reward: Mapped[float] = mapped_column(sa.Float, nullable=False)
    reward_version: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="v1")
    attribution_confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=1.0)
    maturity: Mapped[str] = mapped_column(sa.String(32), nullable=False)
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

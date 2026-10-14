"""Persistence repositories for Phase P9 Analytics & Economics Engine.

Money is written as (amount, currency) and read back as `Money`. A row whose currency
is NULL (stored before ADR-0031) is loaded with its amount in the ``*_unreconciled``
field and no `Money`, so no aggregation can silently treat it as USD.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

from plm_contracts import QualityStatus
from plm_contracts.measurement import ObservationKind
from plm_contracts.money import FxRate, FxTable, Money
from plm_contracts.seams.c7 import ObservationMaturity
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import (
    CostCategory,
    MetricSource,
    MetricStatus,
    ObservationWindow,
    RevenueStatus,
)
from ..domain.ids import build_fx_rate_id
from ..domain.models import (
    CostItem,
    ExperimentOutcomeRecord,
    NormalizedRewardPayload,
    RevenueStatement,
    TrackMetricObservation,
    VideoMetricObservation,
)
from .models import (
    CostItemRow,
    ExperimentOutcomeRow,
    FxRateRow,
    RevenueStatementRow,
    RewardPostedRow,
    TrackMetricRow,
    VideoMetricRow,
)

__all__ = [
    "FxRateConflictError",
    "get_costs_for_entity",
    "get_experiment_outcomes",
    "get_revenue_statement_by_hash",
    "get_track_metrics",
    "get_video_metrics",
    "insert_cost_item",
    "insert_experiment_outcome",
    "insert_fx_rate",
    "insert_posted_reward",
    "insert_revenue_statement",
    "insert_track_metric",
    "insert_video_metric",
    "load_fx_rates",
    "load_fx_table",
]


class FxRateConflictError(ValueError):
    """A rate for the same pair, instant and source is already recorded with another value."""


def _utc(moment: datetime) -> datetime:
    """Stored instants are UTC; a backend that drops the zone (SQLite) gets it back."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _money(amount: Decimal | None, currency: str | None) -> Money | None:
    if amount is None or currency is None:
        return None
    return Money.of(amount, currency)


def _unreconciled(amount: Decimal | None, currency: str | None) -> Decimal | None:
    return amount if amount is not None and currency is None else None


def insert_video_metric(session: Session, obs: VideoMetricObservation) -> None:
    revenue = obs.estimated_revenue
    row = VideoMetricRow(
        id=obs.id,
        video_id=obs.video_id,
        channel_id=obs.channel_id,
        window=obs.window.value,
        date_str=obs.date_str,
        observation_kind=obs.observation_kind.value,
        views=obs.views,
        watch_time_minutes=obs.watch_time_minutes,
        average_view_duration_seconds=obs.average_view_duration_seconds,
        average_percentage_viewed=obs.average_percentage_viewed,
        subscribers_gained=obs.subscribers_gained,
        subscribers_lost=obs.subscribers_lost,
        estimated_revenue_amount=revenue.amount if revenue is not None else None,
        estimated_revenue_currency=revenue.currency if revenue is not None else None,
        revenue_status=obs.revenue_status.value,
        source=obs.source.value,
        status=obs.status.value,
        observed_at=obs.observed_at,
    )
    session.merge(row)


def get_video_metrics(session: Session, video_id: str) -> list[VideoMetricObservation]:
    rows = session.execute(
        select(VideoMetricRow)
        .where(VideoMetricRow.video_id == video_id)
        .order_by(VideoMetricRow.observed_at.desc(), VideoMetricRow.id)
    ).scalars().all()
    return [
        VideoMetricObservation(
            id=r.id,
            video_id=r.video_id,
            channel_id=r.channel_id,
            window=ObservationWindow(r.window),
            date_str=r.date_str,
            observation_kind=ObservationKind(r.observation_kind),
            views=r.views,
            watch_time_minutes=r.watch_time_minutes,
            average_view_duration_seconds=r.average_view_duration_seconds,
            average_percentage_viewed=r.average_percentage_viewed,
            subscribers_gained=r.subscribers_gained,
            subscribers_lost=r.subscribers_lost,
            estimated_revenue=_money(r.estimated_revenue_amount, r.estimated_revenue_currency),
            estimated_revenue_unreconciled=_unreconciled(
                r.estimated_revenue_amount, r.estimated_revenue_currency
            ),
            revenue_status=RevenueStatus(r.revenue_status),
            source=MetricSource(r.source),
            status=MetricStatus(r.status),
            observed_at=_utc(r.observed_at),
        )
        for r in rows
    ]


def insert_track_metric(session: Session, obs: TrackMetricObservation) -> None:
    if obs.reported_revenue is None:
        raise ValueError("a statement line is stored only with its declared currency")
    row = TrackMetricRow(
        id=obs.id,
        isrc=obs.isrc,
        track_id=obs.track_id,
        release_id=obs.release_id,
        window=obs.window.value if obs.window is not None else None,
        date_str=obs.date_str,
        observation_kind=obs.observation_kind.value,
        streams=obs.streams,
        reported_revenue_amount=obs.reported_revenue.amount,
        reported_revenue_currency=obs.reported_revenue.currency,
        provider=obs.provider,
        revenue_status=obs.revenue_status.value,
        source=obs.source.value,
        status=obs.status.value,
        observed_at=obs.observed_at,
    )
    session.merge(row)


def get_track_metrics(session: Session, track_id: str) -> list[TrackMetricObservation]:
    rows = session.execute(
        select(TrackMetricRow)
        .where(TrackMetricRow.track_id == track_id)
        .order_by(TrackMetricRow.observed_at.desc(), TrackMetricRow.id)
    ).scalars().all()
    return [
        TrackMetricObservation(
            id=r.id,
            isrc=r.isrc,
            track_id=r.track_id,
            release_id=r.release_id,
            window=ObservationWindow(r.window) if r.window is not None else None,
            date_str=r.date_str,
            observation_kind=ObservationKind(r.observation_kind),
            streams=r.streams,
            reported_revenue=_money(r.reported_revenue_amount, r.reported_revenue_currency),
            reported_revenue_unreconciled=_unreconciled(
                r.reported_revenue_amount, r.reported_revenue_currency
            ),
            provider=r.provider,
            revenue_status=RevenueStatus(r.revenue_status),
            source=MetricSource(r.source),
            status=MetricStatus(r.status),
            observed_at=_utc(r.observed_at),
        )
        for r in rows
    ]


def insert_revenue_statement(session: Session, stmt: RevenueStatement) -> None:
    row = RevenueStatementRow(
        id=stmt.id,
        provider=stmt.provider,
        period=stmt.period,
        currency=stmt.currency,
        total_revenue_amount=stmt.total_revenue_amount,
        total_streams=stmt.total_streams,
        file_hash=stmt.file_hash,
        source_reference=stmt.source_reference,
        received_at=stmt.received_at,
        unattributed_revenue_amount=stmt.unattributed_revenue_amount,
        unattributed_row_count=stmt.unattributed_row_count,
    )
    session.merge(row)


def get_revenue_statement_by_hash(session: Session, file_hash: str) -> RevenueStatement | None:
    row = session.execute(
        select(RevenueStatementRow).where(RevenueStatementRow.file_hash == file_hash)
    ).scalar_one_or_none()
    if not row:
        return None
    return RevenueStatement(
        id=row.id,
        provider=row.provider,
        period=row.period,
        currency=row.currency,
        total_revenue_amount=row.total_revenue_amount,
        total_streams=row.total_streams,
        file_hash=row.file_hash,
        source_reference=row.source_reference,
        received_at=_utc(row.received_at),
        unattributed_revenue_amount=row.unattributed_revenue_amount,
        unattributed_row_count=row.unattributed_row_count,
    )


def insert_cost_item(session: Session, cost: CostItem) -> None:
    if cost.amount is None:
        raise ValueError("a cost is stored only with its declared currency")
    row = CostItemRow(
        id=cost.id,
        entity_id=cost.entity_id,
        entity_type=cost.entity_type,
        category=cost.category.value,
        amount=cost.amount.amount,
        currency=cost.amount.currency,
        period=cost.period,
        description=cost.description,
        incurred_at=cost.incurred_at,
    )
    session.merge(row)


def get_costs_for_entity(session: Session, entity_id: str) -> list[CostItem]:
    rows = session.execute(
        select(CostItemRow).where(CostItemRow.entity_id == entity_id).order_by(CostItemRow.id)
    ).scalars().all()
    return [
        CostItem(
            id=r.id,
            entity_id=r.entity_id,
            entity_type=r.entity_type,
            category=CostCategory(r.category),
            amount=_money(r.amount, r.currency),
            amount_unreconciled=_unreconciled(r.amount, r.currency),
            period=r.period,
            description=r.description,
            incurred_at=_utc(r.incurred_at),
        )
        for r in rows
    ]


def insert_fx_rate(session: Session, rate: FxRate, *, recorded_at: datetime) -> str:
    """Record one rate; replaying the same rate is a no-op, a different value is refused."""
    as_of = _utc(rate.as_of)
    rate_row_id = build_fx_rate_id(rate.base, rate.quote, as_of.isoformat(), rate.source)
    existing = session.get(FxRateRow, rate_row_id)
    if existing is not None:
        if Decimal(existing.rate) != rate.rate:
            raise FxRateConflictError(
                f"{rate.rate_id} is already recorded as {existing.rate}, not {rate.rate}"
            )
        return rate_row_id
    session.add(
        FxRateRow(
            id=rate_row_id,
            base=rate.base,
            quote=rate.quote,
            rate=rate.rate,
            source=rate.source,
            as_of=as_of,
            recorded_at=_utc(recorded_at),
        )
    )
    session.flush()
    return rate_row_id


def load_fx_rates(session: Session, currencies: Iterable[str] | None = None) -> list[FxRate]:
    stmt = select(FxRateRow).order_by(FxRateRow.base, FxRateRow.quote, FxRateRow.as_of, FxRateRow.id)
    if currencies is not None:
        wanted = sorted(set(currencies))
        stmt = stmt.where(FxRateRow.base.in_(wanted), FxRateRow.quote.in_(wanted))
    return [
        FxRate(base=r.base, quote=r.quote, rate=Decimal(r.rate), source=r.source, as_of=_utc(r.as_of))
        for r in session.execute(stmt).scalars().all()
    ]


def load_fx_table(session: Session, currencies: Iterable[str] | None = None) -> FxTable:
    """Every recorded rate (optionally only between ``currencies``) as an `FxTable`."""
    return FxTable(load_fx_rates(session, currencies))


def insert_experiment_outcome(session: Session, outc: ExperimentOutcomeRecord) -> None:
    row = ExperimentOutcomeRow(
        id=outc.id,
        experiment_id=outc.experiment_id,
        experiment_family_id=outc.experiment_family_id,
        content_arm_id=outc.content_arm_id,
        content_genome_id=outc.content_genome_id,
        unit_id=outc.unit_id,
        observation_window=outc.observation_window.value,
        primary_metric=outc.primary_metric,
        primary_value=outc.primary_value,
        primary_value_currency=outc.primary_value_currency,
        normalized_reward=outc.normalized_reward,
        reward_version=outc.reward_version,
        attribution_confidence=outc.attribution_confidence,
        maturity=outc.maturity.value,
        published_at=outc.published_at,
        quality_status=outc.quality_status.value if outc.quality_status else None,
        created_at=outc.created_at,
    )
    session.merge(row)


def get_experiment_outcomes(session: Session, experiment_id: str) -> list[ExperimentOutcomeRecord]:
    rows = session.execute(
        select(ExperimentOutcomeRow)
        .where(ExperimentOutcomeRow.experiment_id == experiment_id)
        .order_by(ExperimentOutcomeRow.id)
    ).scalars().all()
    return [
        ExperimentOutcomeRecord(
            id=r.id,
            experiment_id=r.experiment_id,
            experiment_family_id=r.experiment_family_id,
            content_arm_id=r.content_arm_id,
            content_genome_id=r.content_genome_id,
            unit_id=r.unit_id,
            observation_window=ObservationWindow(r.observation_window),
            primary_metric=r.primary_metric,
            primary_value=r.primary_value,
            primary_value_currency=r.primary_value_currency,
            normalized_reward=r.normalized_reward,
            reward_version=r.reward_version,
            attribution_confidence=r.attribution_confidence,
            maturity=ObservationMaturity(r.maturity),
            published_at=_utc(r.published_at) if r.published_at is not None else None,
            quality_status=QualityStatus(r.quality_status) if r.quality_status else None,
            created_at=_utc(r.created_at),
        )
        for r in rows
    ]


def insert_posted_reward(session: Session, payload: NormalizedRewardPayload) -> None:
    row = RewardPostedRow(
        id=f"rwd_{payload.experiment_id}_{payload.arm_id}_{payload.observation_window}",
        experiment_id=payload.experiment_id,
        arm_id=payload.arm_id,
        content_id=payload.content_id,
        observation_window=payload.observation_window,
        status=payload.status,
        normalized_reward=payload.normalized_reward,
        reward_version=payload.reward_version,
        attribution_confidence=payload.attribution_confidence,
        raw_metrics=payload.raw_metrics,
        posted_at=payload.observed_at,
    )
    session.merge(row)

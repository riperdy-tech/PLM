"""Persistence repositories for Phase P9 Analytics & Economics Engine."""

from __future__ import annotations

from plm_contracts import QualityStatus
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
    RevenueStatementRow,
    RewardPostedRow,
    TrackMetricRow,
    VideoMetricRow,
)


def insert_video_metric(session: Session, obs: VideoMetricObservation) -> None:
    row = VideoMetricRow(
        id=obs.id,
        video_id=obs.video_id,
        channel_id=obs.channel_id,
        window=obs.window.value,
        date_str=obs.date_str,
        views=obs.views,
        watch_time_minutes=obs.watch_time_minutes,
        average_view_duration_seconds=obs.average_view_duration_seconds,
        average_percentage_viewed=obs.average_percentage_viewed,
        subscribers_gained=obs.subscribers_gained,
        subscribers_lost=obs.subscribers_lost,
        estimated_revenue_usd=obs.estimated_revenue_usd,
        revenue_status=obs.revenue_status.value,
        source=obs.source.value,
        status=obs.status.value,
        observed_at=obs.observed_at,
    )
    session.merge(row)


def get_video_metrics(session: Session, video_id: str) -> list[VideoMetricObservation]:
    rows = session.execute(
        select(VideoMetricRow).where(VideoMetricRow.video_id == video_id).order_by(VideoMetricRow.observed_at.desc())
    ).scalars().all()
    return [
        VideoMetricObservation(
            id=r.id,
            video_id=r.video_id,
            channel_id=r.channel_id,
            window=ObservationWindow(r.window),
            date_str=r.date_str,
            views=r.views,
            watch_time_minutes=r.watch_time_minutes,
            average_view_duration_seconds=r.average_view_duration_seconds,
            average_percentage_viewed=r.average_percentage_viewed,
            subscribers_gained=r.subscribers_gained,
            subscribers_lost=r.subscribers_lost,
            estimated_revenue_usd=r.estimated_revenue_usd,
            revenue_status=RevenueStatus(r.revenue_status),
            source=MetricSource(r.source),
            status=MetricStatus(r.status),
            observed_at=r.observed_at,
        )
        for r in rows
    ]


def insert_track_metric(session: Session, obs: TrackMetricObservation) -> None:
    row = TrackMetricRow(
        id=obs.id,
        isrc=obs.isrc,
        track_id=obs.track_id,
        release_id=obs.release_id,
        window=obs.window.value,
        date_str=obs.date_str,
        streams=obs.streams,
        reported_revenue_usd=obs.reported_revenue_usd,
        provider=obs.provider,
        revenue_status=obs.revenue_status.value,
        source=obs.source.value,
        status=obs.status.value,
        observed_at=obs.observed_at,
    )
    session.merge(row)


def get_track_metrics(session: Session, track_id: str) -> list[TrackMetricObservation]:
    rows = session.execute(
        select(TrackMetricRow).where(TrackMetricRow.track_id == track_id).order_by(TrackMetricRow.observed_at.desc())
    ).scalars().all()
    return [
        TrackMetricObservation(
            id=r.id,
            isrc=r.isrc,
            track_id=r.track_id,
            release_id=r.release_id,
            window=ObservationWindow(r.window),
            date_str=r.date_str,
            streams=r.streams,
            reported_revenue_usd=r.reported_revenue_usd,
            provider=r.provider,
            revenue_status=RevenueStatus(r.revenue_status),
            source=MetricSource(r.source),
            status=MetricStatus(r.status),
            observed_at=r.observed_at,
        )
        for r in rows
    ]


def insert_revenue_statement(session: Session, stmt: RevenueStatement) -> None:
    row = RevenueStatementRow(
        id=stmt.id,
        provider=stmt.provider,
        period=stmt.period,
        total_revenue_usd=stmt.total_revenue_usd,
        total_streams=stmt.total_streams,
        file_hash=stmt.file_hash,
        source_reference=stmt.source_reference,
        received_at=stmt.received_at,
        unattributed_revenue_usd=stmt.unattributed_revenue_usd,
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
        total_revenue_usd=row.total_revenue_usd,
        total_streams=row.total_streams,
        file_hash=row.file_hash,
        source_reference=row.source_reference,
        received_at=row.received_at,
        unattributed_revenue_usd=row.unattributed_revenue_usd,
        unattributed_row_count=row.unattributed_row_count,
    )


def insert_cost_item(session: Session, cost: CostItem) -> None:
    row = CostItemRow(
        id=cost.id,
        entity_id=cost.entity_id,
        entity_type=cost.entity_type,
        category=cost.category.value,
        amount_usd=cost.amount_usd,
        period=cost.period,
        description=cost.description,
        incurred_at=cost.incurred_at,
    )
    session.merge(row)


def get_costs_for_entity(session: Session, entity_id: str) -> list[CostItem]:
    rows = session.execute(
        select(CostItemRow).where(CostItemRow.entity_id == entity_id)
    ).scalars().all()
    return [
        CostItem(
            id=r.id,
            entity_id=r.entity_id,
            entity_type=r.entity_type,
            category=CostCategory(r.category),
            amount_usd=r.amount_usd,
            period=r.period,
            description=r.description,
            incurred_at=r.incurred_at,
        )
        for r in rows
    ]


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
        normalized_reward=outc.normalized_reward,
        reward_version=outc.reward_version,
        attribution_confidence=outc.attribution_confidence,
        maturity=outc.maturity.value,
        quality_status=outc.quality_status.value if outc.quality_status else None,
        created_at=outc.created_at,
    )
    session.merge(row)


def get_experiment_outcomes(session: Session, experiment_id: str) -> list[ExperimentOutcomeRecord]:
    rows = session.execute(
        select(ExperimentOutcomeRow).where(ExperimentOutcomeRow.experiment_id == experiment_id)
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
            normalized_reward=r.normalized_reward,
            reward_version=r.reward_version,
            attribution_confidence=r.attribution_confidence,
            maturity=ObservationMaturity(r.maturity),
            quality_status=QualityStatus(r.quality_status) if r.quality_status else None,
            created_at=r.created_at,
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

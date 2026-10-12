"""YouTube analytics ingestion module (P9 §16, §55)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..domain.enums import MetricSource, MetricStatus, ObservationWindow, RevenueStatus
from ..domain.ids import build_observation_id
from ..domain.models import VideoMetricObservation
from ..storage.repositories import insert_video_metric


def ingest_youtube_metrics(
    session: Session,
    *,
    video_id: str,
    channel_id: str,
    date_str: str,
    views: int,
    watch_time_minutes: float,
    average_view_duration_seconds: float = 0.0,
    average_percentage_viewed: float = 0.0,
    subscribers_gained: int = 0,
    subscribers_lost: int = 0,
    estimated_revenue_usd: float = 0.0,
    window: ObservationWindow = ObservationWindow.W_24H,
    source: MetricSource = MetricSource.YOUTUBE_ANALYTICS,
    status: MetricStatus = MetricStatus.VALID,
    observed_at: datetime | None = None,
) -> VideoMetricObservation:
    """Ingest and normalize YouTube video metrics."""
    now = observed_at or datetime.now(UTC)
    obs_id = build_observation_id(video_id, "views", window.value, date_str)

    obs = VideoMetricObservation(
        id=obs_id,
        video_id=video_id,
        channel_id=channel_id,
        window=window,
        date_str=date_str,
        views=views,
        watch_time_minutes=watch_time_minutes,
        average_view_duration_seconds=average_view_duration_seconds,
        average_percentage_viewed=average_percentage_viewed,
        subscribers_gained=subscribers_gained,
        subscribers_lost=subscribers_lost,
        estimated_revenue_usd=estimated_revenue_usd,
        revenue_status=RevenueStatus.ESTIMATED,
        source=source,
        status=status,
        observed_at=now,
    )

    insert_video_metric(session, obs)
    return obs

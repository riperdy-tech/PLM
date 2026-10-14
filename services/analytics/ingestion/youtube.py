"""YouTube analytics ingestion module (P9 §16, §55, ADR-0031).

Each call stores one cumulative snapshot of a video for one window. Every metric that
was not measured is ``None`` and stored as NULL, never ``0``. Estimated revenue is a
platform estimate (``revenue_status = estimated``, P9 §111) and must arrive with the
currency YouTube reported it in; no currency is assumed.
"""

from __future__ import annotations

from datetime import datetime

from plm_contracts.measurement import ObservationKind
from plm_contracts.money import Money
from plm_platform.clock import require_aware
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
    window: ObservationWindow,
    observed_at: datetime,
    views: int | None = None,
    watch_time_minutes: float | None = None,
    average_view_duration_seconds: float | None = None,
    average_percentage_viewed: float | None = None,
    subscribers_gained: int | None = None,
    subscribers_lost: int | None = None,
    estimated_revenue: Money | None = None,
    source: MetricSource = MetricSource.YOUTUBE_ANALYTICS,
    status: MetricStatus = MetricStatus.VALID,
) -> VideoMetricObservation:
    """Store one cumulative snapshot of a video for ``window`` as of ``observed_at``.

    The identifying facts (video, channel, date, window, observation instant) have no
    defaults. Metric arguments left as ``None`` were not measured.
    """
    now = require_aware(observed_at)
    obs_id = build_observation_id(video_id, "views", window.value, date_str)

    obs = VideoMetricObservation(
        id=obs_id,
        video_id=video_id,
        channel_id=channel_id,
        window=window,
        date_str=date_str,
        observation_kind=ObservationKind.CUMULATIVE_SNAPSHOT,
        views=views,
        watch_time_minutes=watch_time_minutes,
        average_view_duration_seconds=average_view_duration_seconds,
        average_percentage_viewed=average_percentage_viewed,
        subscribers_gained=subscribers_gained,
        subscribers_lost=subscribers_lost,
        estimated_revenue=estimated_revenue,
        revenue_status=RevenueStatus.ESTIMATED,
        source=source,
        status=status,
        observed_at=now,
    )

    insert_video_metric(session, obs)
    return obs

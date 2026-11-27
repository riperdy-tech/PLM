"""YouTube Analytics for a published video: the source P9 measures from (R5, R6).

* :class:`MockYouTubeAnalytics` answers from the deterministic outcome model
  (:mod:`plm_platform.testing.mock_outcomes`), keyed by the publication's stored identity
  (video id, arm) and the instants the caller passes. It declares ``provider_mode =
  mock``, and every answer carries it, so P9 stores the numbers as ``synthetic``.
* :class:`RealYouTubeAnalytics` wraps P9's ``RealYouTubeAnalyticsClient`` (Analytics API
  v2, read-only). YouTube Analytics counts days in Pacific time and its numbers settle
  2-3 days later, so a window is read as whole Pacific days and is refused as
  :class:`AnalyticsNotFinal` until :data:`FINALITY_LAG` after its last day (review
  2026-11-26 #35). A metric YouTube did not report is ``None``, never 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Final, Protocol
from zoneinfo import ZoneInfo

from plm_contracts import ObservationWindow, ProviderMode
from plm_contracts.measurement import window_duration
from plm_contracts.money import Money
from plm_platform.testing.mock_outcomes import MockOutcomeModel, MockVideoSnapshot

from .domain.models import YouTubePublicationRecord

__all__ = [
    "ANALYTICS_PACIFIC",
    "FINALITY_LAG",
    "AnalyticsNotFinal",
    "AnalyticsUnavailable",
    "MockYouTubeAnalytics",
    "RealYouTubeAnalytics",
    "VideoAnalyticsSnapshot",
    "YouTubeAnalyticsSource",
    "pacific_report_days",
]

ANALYTICS_PACIFIC: Final[ZoneInfo] = ZoneInfo("America/Los_Angeles")
"""YouTube Analytics' day boundary."""

FINALITY_LAG: Final[timedelta] = timedelta(days=3)
"""How long after a Pacific day ends its Analytics numbers are taken as final."""


class AnalyticsNotFinal(Exception):
    """The window's Analytics numbers are not final yet; ask again at ``ready_at``."""

    def __init__(self, ready_at: datetime) -> None:
        self.ready_at = ready_at
        super().__init__(f"YouTube Analytics for this window is final at {ready_at.isoformat()}")


class AnalyticsUnavailable(Exception):
    """YouTube Analytics could not be read now (the request may succeed later)."""


@dataclass(frozen=True, slots=True)
class VideoAnalyticsSnapshot:
    """One window of a video as YouTube Analytics reported it; ``None`` is unreported."""

    views: int | None
    watch_time_minutes: Decimal | None
    average_view_duration_seconds: Decimal | None
    average_percentage_viewed: Decimal | None
    subscribers_gained: int | None
    subscribers_lost: int | None
    estimated_revenue: Money | None


class YouTubeAnalyticsSource(Protocol):
    provider_mode: ProviderMode
    source_name: str

    @property
    def model_version(self) -> str | None: ...

    def snapshot(self, record: YouTubePublicationRecord, *, window: ObservationWindow,
                 observed_at: datetime,
                 published_at: datetime) -> MockVideoSnapshot | VideoAnalyticsSnapshot:
        """``published_at`` is when the window starts: YouTube's own publish time for a
        real source (it may differ from the record's, review #33)."""
        ...


class MockYouTubeAnalytics:
    """Deterministic, arm-dependent video metrics; labelled mock."""

    provider_mode = ProviderMode.MOCK
    source_name = "mock_youtube_analytics"

    def __init__(self, model: MockOutcomeModel) -> None:
        self.model = model

    @property
    def model_version(self) -> str | None:
        return self.model.model_version

    def snapshot(self, record: YouTubePublicationRecord, *, window: ObservationWindow,
                 observed_at: datetime, published_at: datetime) -> MockVideoSnapshot:
        return self.model.video_snapshot(
            video_id=record.youtube_video_id, arm_id=record.content_arm_id,
            published_at=published_at, window=window, observed_at=observed_at)


def _midnight_after(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(0), tzinfo=ANALYTICS_PACIFIC)


def pacific_report_days(published_at: datetime, window: ObservationWindow,
                        observed_at: datetime) -> tuple[date, date]:
    """The Pacific days ``(start, end)`` a window is read over, inclusive.

    A bounded window covers every Pacific day it touches; ``lifetime`` ends on the
    latest day that is final at ``observed_at``. Raises :class:`AnalyticsNotFinal` when
    the last day is not final at ``observed_at`` (its end plus :data:`FINALITY_LAG`).
    """
    start = published_at.astimezone(ANALYTICS_PACIFIC).date()
    duration = window_duration(window)
    if duration is None:
        end = (observed_at - FINALITY_LAG).astimezone(ANALYTICS_PACIFIC).date() - timedelta(
            days=1)
        if end < start:
            raise AnalyticsNotFinal(_midnight_after(start) + FINALITY_LAG)
        return start, end
    last_instant = published_at + duration - timedelta(microseconds=1)
    end = last_instant.astimezone(ANALYTICS_PACIFIC).date()
    ready_at = _midnight_after(end) + FINALITY_LAG
    if observed_at < ready_at:
        raise AnalyticsNotFinal(ready_at)
    return start, end


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class RealYouTubeAnalytics:
    """P7's real Analytics source over P9's ``RealYouTubeAnalyticsClient`` (read-only).

    ``client`` has ``fetch_video_metrics(video_id=, start_date=, end_date=)`` returning
    ``YouTubeVideoMetrics`` and raising ``YouTubeAnalyticsError``. Estimated revenue is
    not read (it needs the monetary scope and a monetized channel): ``None``.
    """

    provider_mode = ProviderMode.REAL
    source_name = "youtube_analytics"

    def __init__(self, client: object, *, unavailable: type[Exception]) -> None:
        self._client = client
        self._unavailable = unavailable

    @property
    def model_version(self) -> str | None:
        return None

    def snapshot(self, record: YouTubePublicationRecord, *, window: ObservationWindow,
                 observed_at: datetime, published_at: datetime) -> VideoAnalyticsSnapshot:
        start, end = pacific_report_days(published_at, window, observed_at)
        try:
            metrics = self._client.fetch_video_metrics(  # type: ignore[attr-defined]
                video_id=record.youtube_video_id, start_date=start.isoformat(),
                end_date=end.isoformat())
        except self._unavailable as exc:
            raise AnalyticsUnavailable(str(exc)) from exc
        return VideoAnalyticsSnapshot(
            views=metrics.views,
            watch_time_minutes=_decimal(metrics.watch_time_minutes),
            average_view_duration_seconds=_decimal(metrics.average_view_duration_seconds),
            average_percentage_viewed=_decimal(metrics.average_percentage_viewed),
            subscribers_gained=metrics.subscribers_gained,
            subscribers_lost=metrics.subscribers_lost,
            estimated_revenue=None,
        )

"""Production client for YouTube Analytics API v2 (Phase P8/P9 / R6)."""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

import httpx
from plm_contracts import ProviderMode
from plm_platform.clock import Clock
from plm_platform.dotenv import load_env_file

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL: Final[str] = "https://oauth2.googleapis.com/token"
YOUTUBE_ANALYTICS_API_BASE: Final[str] = "https://youtubeanalytics.googleapis.com/v2/reports"


class YouTubeAnalyticsError(RuntimeError):
    """Raised when communication with YouTube Analytics API fails."""


@dataclass(frozen=True, slots=True)
class YouTubeVideoMetrics:
    """Snapshot of video performance from YouTube Analytics API."""

    video_id: str
    start_date: str
    end_date: str
    views: int | None
    watch_time_minutes: float | None
    average_view_duration_seconds: float | None
    average_percentage_viewed: float | None
    subscribers_gained: int | None
    subscribers_lost: int | None


@dataclass(frozen=True, slots=True)
class YouTubeTrafficSource:
    """One distinct Analytics traffic source (not a thumbnail impression)."""

    source_type: str
    views: int
    watch_time_minutes: float


@dataclass(frozen=True, slots=True)
class YouTubeTrafficSourcesReport:
    """One video's source breakdown; no rows means no report, not zero traffic."""

    video_id: str
    start_date: str
    end_date: str
    rows: tuple[YouTubeTrafficSource, ...]


@dataclass(frozen=True, slots=True)
class YouTubeRetentionPoint:
    """One elapsed-video fraction and its measured audience-retention ratios."""

    elapsed_video_fraction: float
    audience_watch_ratio: float
    relative_retention_performance: float


@dataclass(frozen=True, slots=True)
class YouTubeRetentionReport:
    """Unaggregated YouTube retention curve; empty means unavailable, not zero."""

    video_id: str
    start_date: str
    end_date: str
    points: tuple[YouTubeRetentionPoint, ...]


class RealYouTubeAnalyticsClient:
    """Production client for querying YouTube Analytics API v2 using OAuth 2.0."""

    provider_mode = ProviderMode.REAL

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        timeout_seconds: float = 30.0,
        clock: Clock | None = None,
    ) -> None:
        dotenv = load_env_file(Path(".env"))
        if client_id is None:
            client_id = os.getenv("YOUTUBE_OAUTH_CLIENT_ID") or dotenv.get("YOUTUBE_OAUTH_CLIENT_ID", "")
        self.client_id = client_id

        if client_secret is None:
            client_secret = os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET") or dotenv.get("YOUTUBE_OAUTH_CLIENT_SECRET", "")
        self.client_secret = client_secret

        if refresh_token is None:
            refresh_token = os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN") or dotenv.get("YOUTUBE_OAUTH_REFRESH_TOKEN", "")
        self.refresh_token = refresh_token

        self.timeout = timeout_seconds
        self._clock = clock


        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        # The refresh response may omit scope; absence is never proof of permission.
        self._reported_oauth_scopes: frozenset[str] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def reporting_scope_status(self) -> str:
        """Use only scope metadata received with the latest OAuth refresh.

        No extra API calls, consent changes, or scope requests are made.
        """
        scopes = self._reported_oauth_scopes
        if scopes is None:
            return "not_reported_by_oauth"
        required = {
            "https://www.googleapis.com/auth/yt-analytics.readonly",
            "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
        }
        return "required_scope_reported" if scopes & required else "required_scope_not_reported"

    def _get_access_token(self) -> str:
        """Fetch or refresh OAuth access token using refresh token."""
        if not self.is_configured:
            raise YouTubeAnalyticsError("YouTube OAuth credentials not configured")

        if self._access_token and time.monotonic() < self._access_token_expires_at - 60:
            return self._access_token

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise YouTubeAnalyticsError(
                    f"OAuth token refresh rejected (HTTP {resp.status_code})"
                )

            data = resp.json()
            token = data.get("access_token")
            if not token or not isinstance(token, str):
                raise YouTubeAnalyticsError("OAuth token response omitted access_token")

            expires_in = float(data.get("expires_in", 3600))
            raw_scope = data.get("scope")
            self._reported_oauth_scopes = (
                frozenset(raw_scope.split()) if isinstance(raw_scope, str) and raw_scope.strip()
                else None
            )
            self._access_token = token
            self._access_token_expires_at = time.monotonic() + expires_in
            return token

    def _auth_headers(self) -> dict[str, str]:
        token = self._get_access_token()
        return {"Authorization": f"Bearer {token}"}

    def fetch_video_metrics(
        self,
        *,
        video_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        channel_id: str = "MINE",
    ) -> YouTubeVideoMetrics:
        """Fetch performance metrics for a video from YouTube Analytics API."""
        if start_date is not None and end_date is not None:

            s_date = start_date
            e_date = end_date
        elif self._clock is not None:
            ref_dt = self._clock.now()
            s_date = start_date or (ref_dt - timedelta(days=28)).strftime("%Y-%m-%d")
            e_date = end_date or ref_dt.strftime("%Y-%m-%d")
        else:
            raise ValueError("start_date and end_date are required when no Clock is configured")


        params = {
            "ids": f"channel=={channel_id}",
            "startDate": s_date,
            "endDate": e_date,
            "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost",
            "filters": f"video=={video_id}",
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(YOUTUBE_ANALYTICS_API_BASE, headers=self._auth_headers(), params=params)
            if resp.status_code != 200:
                raise YouTubeAnalyticsError(
                    f"YouTube Analytics API query failed (HTTP {resp.status_code})"
                )
            data = resp.json()

        headers_list = [h.get("name") for h in data.get("columnHeaders", [])]
        rows = data.get("rows", [])

        if not rows:
            # Video has no analytics rows yet (e.g. freshly published or zero views)
            return YouTubeVideoMetrics(
                video_id=video_id,
                start_date=s_date,
                end_date=e_date,
                views=None,
                watch_time_minutes=None,
                average_view_duration_seconds=None,
                average_percentage_viewed=None,
                subscribers_gained=None,
                subscribers_lost=None,
            )

        row = rows[0]
        row_map = dict(zip(headers_list, row, strict=False))

        def _get_int(key: str) -> int | None:
            val = row_map.get(key)
            return int(val) if val is not None else None

        def _get_float(key: str) -> float | None:
            val = row_map.get(key)
            return float(val) if val is not None else None

        return YouTubeVideoMetrics(
            video_id=video_id,
            start_date=s_date,
            end_date=e_date,
            views=_get_int("views"),
            watch_time_minutes=_get_float("estimatedMinutesWatched"),
            average_view_duration_seconds=_get_float("averageViewDuration"),
            average_percentage_viewed=_get_float("averageViewPercentage"),
            subscribers_gained=_get_int("subscribersGained"),
            subscribers_lost=_get_int("subscribersLost"),
        )

    def fetch_video_traffic_sources(
        self,
        *,
        video_id: str,
        start_date: str,
        end_date: str,
        channel_id: str = "MINE",
    ) -> YouTubeTrafficSourcesReport:
        """Read official traffic-source breakdown for one video and fixed date window.

        Analytics reports.query supports insightTrafficSourceType with views and
        estimatedMinutesWatched, *not* video-thumbnail impressions/CTR.
        Sources are returned as given by Google; no unknown categories or totals
        are manufactured. Empty rows remain unavailable.
        """
        if not video_id.strip():
            raise ValueError("video_id is required")
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        params = {
            "ids": f"channel=={channel_id}",
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": "insightTrafficSourceType",
            "metrics": "views,estimatedMinutesWatched",
            "filters": f"video=={video_id}",
            "sort": "-views",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                YOUTUBE_ANALYTICS_API_BASE,
                headers=self._auth_headers(),
                params=params,
            )
        if response.status_code != 200:
            raise YouTubeAnalyticsError(
                f"YouTube traffic-source query failed (HTTP {response.status_code})"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise YouTubeAnalyticsError("traffic-source response is not an object")
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise YouTubeAnalyticsError("traffic-source rows are not a list")
        if not rows:
            return YouTubeTrafficSourcesReport(video_id, start_date, end_date, ())
        headers = payload.get("columnHeaders", [])
        if not isinstance(headers, list) or any(not isinstance(h, dict) for h in headers):
            raise YouTubeAnalyticsError("traffic-source headers are invalid")
        names = [h.get("name") for h in headers]
        required = ("insightTrafficSourceType", "views", "estimatedMinutesWatched")
        if len(names) != len(set(names)) or any(name not in names for name in required):
            raise YouTubeAnalyticsError("traffic-source report is missing required dimensions/metrics")
        seen: set[str] = set()
        measured: list[YouTubeTrafficSource] = []
        for raw in rows:
            if not isinstance(raw, list) or len(raw) != len(names):
                raise YouTubeAnalyticsError("invalid traffic-source row width")
            item = dict(zip(names, raw, strict=True))
            source = item["insightTrafficSourceType"]
            original_views = item["views"]
            try:
                views = int(original_views)
                minutes = float(item["estimatedMinutesWatched"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise YouTubeAnalyticsError("traffic-source measurement is invalid") from exc
            if (
                not isinstance(source, str) or not source.strip() or source in seen
                or isinstance(original_views, bool) or isinstance(original_views, float)
                and not original_views.is_integer()
                or views < 0 or not math.isfinite(minutes) or minutes < 0
            ):
                raise YouTubeAnalyticsError("traffic-source measurement out of range or duplicated")
            seen.add(source)
            measured.append(YouTubeTrafficSource(source, views, minutes))
        return YouTubeTrafficSourcesReport(video_id, start_date, end_date, tuple(measured))

    def fetch_video_retention(
        self,
        *,
        video_id: str,
        start_date: str,
        end_date: str,
        channel_id: str = "MINE",
    ) -> YouTubeRetentionReport:
        """Read a single video's official relative/absolute retention curve.

        Missing rows are an empty report rather than fabricated 0% retention.
        This method is read-only and does not update P2 learning outcomes.
        """
        if not video_id.strip():
            raise ValueError("video_id is required")
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        params = {
            "ids": f"channel=={channel_id}",
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": "elapsedVideoTimeRatio",
            "metrics": "audienceWatchRatio,relativeRetentionPerformance",
            "filters": f"video=={video_id}",
            "sort": "elapsedVideoTimeRatio",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                YOUTUBE_ANALYTICS_API_BASE,
                headers=self._auth_headers(),
                params=params,
            )
        if response.status_code != 200:
            raise YouTubeAnalyticsError(
                f"YouTube retention query failed (HTTP {response.status_code})"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise YouTubeAnalyticsError("retention response is not an object")
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise YouTubeAnalyticsError("retention rows are not a list")
        if not rows:
            return YouTubeRetentionReport(video_id, start_date, end_date, ())
        headers = payload.get("columnHeaders", [])
        names = [h.get("name") for h in headers if isinstance(h, dict)]
        required = ("elapsedVideoTimeRatio", "audienceWatchRatio", "relativeRetentionPerformance")
        if len(names) != len(headers) or any(key not in names for key in required):
            raise YouTubeAnalyticsError("retention report is missing required dimensions or metrics")
        points: list[YouTubeRetentionPoint] = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(names):
                raise YouTubeAnalyticsError("invalid retention row width")
            values = dict(zip(names, row, strict=True))
            try:
                fraction, absolute, relative = (float(values[key]) for key in required)
            except (TypeError, ValueError) as exc:
                raise YouTubeAnalyticsError("invalid retention measurement") from exc
            if (not all(math.isfinite(x) for x in (fraction, absolute, relative))
                    or not 0 <= fraction <= 1 or absolute < 0
                    or not 0 <= relative <= 1):
                raise YouTubeAnalyticsError("retention point is out of range")
            if points and fraction <= points[-1].elapsed_video_fraction:
                raise YouTubeAnalyticsError("retention points are not strictly ascending")
            points.append(YouTubeRetentionPoint(fraction, absolute, relative))
        return YouTubeRetentionReport(video_id, start_date, end_date, tuple(points))

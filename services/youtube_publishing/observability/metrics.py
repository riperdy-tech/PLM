"""Prometheus metrics for Phase P7 Autonomous YouTube Publishing Engine."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

PUBLICATIONS_TOTAL = Counter(
    "plm_youtube_publications_total",
    "Total YouTube publication attempts by terminal state",
    ["channel_id", "state"],
)

UPLOAD_DURATION_SECONDS = Histogram(
    "plm_youtube_upload_duration_seconds",
    "Duration of video upload operations in seconds",
    ["channel_id"],
)

QUOTA_UNITS_CONSUMED = Counter(
    "plm_youtube_quota_units_consumed_total",
    "Estimated YouTube API quota units consumed",
    ["operation"],
)

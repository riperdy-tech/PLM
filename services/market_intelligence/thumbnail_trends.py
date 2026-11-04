"""Rank recent niche videos by how far they out-performed their channel (thumbnail trend scan).

A video with many more views than its channel has subscribers was found by people who
did not already follow the channel: its packaging (thumbnail and title) did the work.
Those outliers are what the thumbnail style guide (``config/thumbnail_styles.json``) is
reviewed against. Pure functions over the P1 connector's parsed results; the scan script
(``scripts/thumbnail_trend_scan.py``) does the I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .connectors.base import FetchResult
from .connectors.youtube import ChannelStats, VideoStats

__all__ = ["TrendEntry", "rank_outliers", "thumbnail_urls"]

_THUMBNAIL_SIZES = ("maxres", "standard", "high", "medium", "default")


@dataclass(frozen=True, slots=True)
class TrendEntry:
    video_id: str
    channel_id: str
    channel_title: str | None
    title: str | None
    published_at: datetime
    view_count: int
    subscriber_count: int
    duration_seconds: float
    thumbnail_url: str | None
    outlier_ratio: float
    """Views per subscriber, with the subscriber count floored (see :func:`rank_outliers`)."""
    views_per_day: float


def thumbnail_urls(fetch: FetchResult) -> dict[str, str]:
    """The largest thumbnail URL each videos.list item declares."""
    urls: dict[str, str] = {}
    for obj in fetch.objects:
        thumbs: Mapping[str, Any] = (obj.payload.get("snippet") or {}).get("thumbnails") or {}
        for size in _THUMBNAIL_SIZES:
            url = (thumbs.get(size) or {}).get("url")
            if url:
                urls[obj.provider_object_id] = str(url)
                break
    return urls


def rank_outliers(
    videos: Sequence[VideoStats],
    channels: Mapping[str, ChannelStats],
    thumbnails: Mapping[str, str],
    *,
    now: datetime,
    min_duration_seconds: float = 1200.0,
    subscriber_floor: int = 1000,
    per_channel: int = 2,
    limit: int = 30,
) -> list[TrendEntry]:
    """Long-form videos ranked by views per subscriber, best first.

    A video is skipped when a fact the ratio needs is unknown (hidden subscriber count,
    missing views, duration or publish time): it is never ranked on a guessed number.
    Subscribers are floored at ``subscriber_floor`` so a brand-new channel's tiny count
    does not make any view total look like a breakout. At most ``per_channel`` videos
    per channel are kept, so one channel's house style cannot fill the sheet.
    """
    entries: list[TrendEntry] = []
    seen: set[str] = set()
    for video in videos:
        if video.video_id in seen:
            continue
        seen.add(video.video_id)
        channel = channels.get(video.channel_id or "")
        if (channel is None or channel.subscriber_count is None or video.view_count is None
                or video.duration_seconds is None or video.published_at is None
                or video.duration_seconds < min_duration_seconds):
            continue
        days = max((now - video.published_at).total_seconds() / 86400.0, 1.0)
        entries.append(TrendEntry(
            video_id=video.video_id,
            channel_id=channel.channel_id,
            channel_title=channel.title,
            title=video.title,
            published_at=video.published_at,
            view_count=video.view_count,
            subscriber_count=channel.subscriber_count,
            duration_seconds=video.duration_seconds,
            thumbnail_url=thumbnails.get(video.video_id),
            outlier_ratio=video.view_count / max(channel.subscriber_count, subscriber_floor),
            views_per_day=video.view_count / days,
        ))
    entries.sort(key=lambda e: (-e.outlier_ratio, -e.views_per_day, e.video_id))
    kept: list[TrendEntry] = []
    per: dict[str, int] = {}
    for entry in entries:
        if per.get(entry.channel_id, 0) >= per_channel:
            continue
        per[entry.channel_id] = per.get(entry.channel_id, 0) + 1
        kept.append(entry)
        if len(kept) >= limit:
            break
    return kept

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from market_intelligence.connectors.base import FetchedObject, FetchResult
from market_intelligence.connectors.youtube import ChannelStats, VideoStats
from market_intelligence.domain.enums import ProviderObjectType
from market_intelligence.thumbnail_trends import rank_outliers, thumbnail_urls

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def _video(vid: str, channel: str, views: int | None, *, minutes: float = 60,
           days_old: int = 10) -> VideoStats:
    return VideoStats(video_id=vid, channel_id=channel, title=vid, description=None,
                      published_at=NOW - timedelta(days=days_old), view_count=views,
                      like_count=None, comment_count=None, duration_seconds=minutes * 60,
                      language=None)


def _channel(cid: str, subs: int | None) -> ChannelStats:
    return ChannelStats(channel_id=cid, title=cid, subscriber_count=subs, video_count=None,
                        view_count=None, country=None, language=None)


def test_outliers_rank_by_views_per_subscriber_not_raw_views() -> None:
    channels = {"big": _channel("big", 1_000_000), "small": _channel("small", 5_000)}
    ranked = rank_outliers([_video("v_big", "big", 500_000), _video("v_small", "small", 100_000)],
                           channels, {}, now=NOW)
    assert [e.video_id for e in ranked] == ["v_small", "v_big"]
    assert ranked[0].outlier_ratio == 20.0


def test_unknown_facts_and_short_videos_are_skipped_never_guessed() -> None:
    channels = {"hidden": _channel("hidden", None), "c": _channel("c", 10_000)}
    ranked = rank_outliers(
        [_video("hidden_subs", "hidden", 9_000), _video("no_views", "c", None),
         _video("short", "c", 90_000, minutes=8), _video("ok", "c", 20_000)],
        channels, {}, now=NOW)
    assert [e.video_id for e in ranked] == ["ok"]


def test_a_tiny_channel_is_floored_and_one_channel_cannot_fill_the_sheet() -> None:
    channels = {"new": _channel("new", 10), "c": _channel("c", 10_000)}
    videos = [_video(f"c{i}", "c", 50_000 + i) for i in range(5)] + [_video("n", "new", 3_000)]
    ranked = rank_outliers(videos, channels, {}, now=NOW, per_channel=2)
    assert ranked[-1].video_id == "n" and ranked[-1].outlier_ratio == 3.0
    assert sum(1 for e in ranked if e.channel_id == "c") == 2


def test_thumbnail_urls_take_the_largest_declared_size() -> None:
    fetch = FetchResult(objects=(FetchedObject(
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="v1",
        observed_at=NOW, payload={"snippet": {"thumbnails": {
            "medium": {"url": "https://i.ytimg.com/vi/v1/mqdefault.jpg"},
            "high": {"url": "https://i.ytimg.com/vi/v1/hqdefault.jpg"}}}}),))
    assert thumbnail_urls(fetch) == {"v1": "https://i.ytimg.com/vi/v1/hqdefault.jpg"}

"""The growth spec's daily decision rules (section 7.3, ADR-0045 build order) per video.

Read-only: it only labels videos from facts YouTube reported (thumbnail impressions and
CTR from the Reporting API reach job, average view duration from the Analytics API). It
never edits a video and never feeds P2. The thresholds are the spec's heuristics, to be
recalibrated against the channel's own medians after about 30 videos.

Each video is compared with the medians of the *other* videos that have the same fact,
so one outlier does not set its own bar. Verdicts, checked in this order:

* ``too_early``: under 3 days old (the spec's 72 h).
* ``no_baseline``: fewer than 3 other videos to compare with.
* ``starved``: first-week impressions under half the others' median while CTR and view
  duration are not weak: YouTube stopped showing it; rotate the series or scenario.
* ``low_data``: under 1,000 impressions: too few to judge packaging or content.
* ``packaging``: CTR under 0.7x median while view duration is at least the median:
  the title/thumbnail is the problem; run a Test & Compare.
* ``content``: CTR at least the median but view duration under 0.6x median: the video
  does not keep the promise; look at the retention curve.
* ``double_down``: a week or older with watch minutes per 1,000 impressions over 2x
  median: make 2-3 sibling videos.
* ``ok``: none of the above.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Final

from ..ingestion.youtube_reach import ReachRow

__all__ = ["ChannelVerdict", "VideoFacts", "evaluate_channel", "first_week_impressions"]

MIN_AGE_DAYS: Final[int] = 3
MIN_IMPRESSIONS: Final[int] = 1000
MIN_OTHERS: Final[int] = 3
STARVED_SHARE: Final[float] = 0.5
WEAK_CTR_SHARE: Final[float] = 0.7
WEAK_DURATION_SHARE: Final[float] = 0.6
DOUBLE_DOWN_SHARE: Final[float] = 2.0
DOUBLE_DOWN_AGE_DAYS: Final[int] = 7

ADVICE: Final[dict[str, str]] = {
    "too_early": "under 3 days old; judge it later",
    "no_baseline": "fewer than 3 other videos to compare with",
    "starved": "YouTube barely showed it; rotate the series or scenario, check the variation gate",
    "low_data": "under 1,000 impressions; too few to judge packaging or content",
    "packaging": "people who click stay, but few click; start a Test & Compare",
    "content": "people click but leave early; check the retention curve and the opening",
    "double_down": "far above the channel's watch time per impression; make 2-3 sibling videos",
    "ok": "in line with the channel",
}


@dataclass(frozen=True, slots=True)
class VideoFacts:
    video_id: str
    title: str
    age_days: int
    impressions: int | None
    """Thumbnail impressions since publication (Reporting API); ``None`` when unknown."""
    ctr_pct: float | None
    avg_view_minutes: float | None
    first_week_impressions: int | None


@dataclass(frozen=True, slots=True)
class ChannelVerdict:
    video_id: str
    title: str
    wti: float | None
    """Watch minutes per 1,000 impressions: 1,000 x CTR x average view duration."""
    verdict: str
    advice: str


def _wti(video: VideoFacts) -> float | None:
    if video.ctr_pct is None or video.avg_view_minutes is None:
        return None
    return round(1000 * video.ctr_pct / 100 * video.avg_view_minutes, 1)


def _median_of(values: Iterable[float | int | None]) -> float | None:
    known = [float(v) for v in values if v is not None]
    return median(known) if len(known) >= MIN_OTHERS else None


def _verdict(video: VideoFacts, others: Sequence[VideoFacts]) -> str:
    if video.age_days < MIN_AGE_DAYS:
        return "too_early"
    ctr_median = _median_of(o.ctr_pct for o in others)
    duration_median = _median_of(o.avg_view_minutes for o in others)
    week_median = _median_of(o.first_week_impressions for o in others)
    if ctr_median is None and duration_median is None and week_median is None:
        return "no_baseline"
    ctr_weak = (video.ctr_pct is not None and ctr_median is not None
                and video.ctr_pct < WEAK_CTR_SHARE * ctr_median)
    duration_weak = (video.avg_view_minutes is not None and duration_median is not None
                     and video.avg_view_minutes < WEAK_DURATION_SHARE * duration_median)
    if (video.age_days >= DOUBLE_DOWN_AGE_DAYS and video.first_week_impressions is not None
            and week_median is not None
            and video.first_week_impressions < STARVED_SHARE * week_median
            and not ctr_weak and not duration_weak):
        return "starved"
    if video.impressions is None or video.impressions < MIN_IMPRESSIONS:
        return "low_data"
    if (ctr_weak and video.avg_view_minutes is not None and duration_median is not None
            and video.avg_view_minutes >= duration_median):
        return "packaging"
    if (duration_weak and video.ctr_pct is not None and ctr_median is not None
            and video.ctr_pct >= ctr_median):
        return "content"
    wti, wti_median = _wti(video), _median_of(_wti(o) for o in others)
    if (video.age_days >= DOUBLE_DOWN_AGE_DAYS and wti is not None and wti_median is not None
            and wti > DOUBLE_DOWN_SHARE * wti_median):
        return "double_down"
    return "ok"


def evaluate_channel(videos: Sequence[VideoFacts]) -> list[ChannelVerdict]:
    """One verdict per video, each against the other videos' medians."""
    results = []
    for video in videos:
        others = [o for o in videos if o.video_id != video.video_id]
        verdict = _verdict(video, others)
        results.append(ChannelVerdict(video.video_id, video.title, _wti(video), verdict,
                                      ADVICE[verdict]))
    return results


def first_week_impressions(rows: Iterable[ReachRow], *, video_id: str,
                           published: date) -> int | None:
    """Impressions on the publication day and the 6 days after; ``None`` without rows."""
    last = published + timedelta(days=6)
    days = [r.impressions for r in rows if r.video_id == video_id and published <= r.date <= last]
    return sum(days) if days else None

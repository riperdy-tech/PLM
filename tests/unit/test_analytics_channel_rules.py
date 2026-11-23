"""The growth spec's daily decision rules (spec section 7.3) on per-video reach facts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from services.analytics.evaluation.channel_rules import (
    VideoFacts,
    evaluate_channel,
    first_week_impressions,
)
from services.analytics.ingestion.youtube_reach import ReachRow


def _video(video_id: str, *, age: int = 20, impressions: int | None = 5000,
           ctr: float | None = 4.0, avd_min: float | None = 10.0,
           week: int | None = 3000) -> VideoFacts:
    return VideoFacts(video_id=video_id, title=video_id, age_days=age, impressions=impressions,
                      ctr_pct=ctr, avg_view_minutes=avd_min, first_week_impressions=week)


def _verdicts(*videos: VideoFacts) -> dict[str, str]:
    return {r.video_id: r.verdict for r in evaluate_channel(videos)}


def test_watch_minutes_per_1000_impressions() -> None:
    """WTI = 1,000 impressions x CTR x average view duration (the spec's core KPI)."""
    report = evaluate_channel([_video("a", ctr=12.0, avd_min=7.0)])[0]
    assert report.wti == 840.0
    assert evaluate_channel([_video("b", ctr=None)])[0].wti is None


def test_young_video_is_too_early() -> None:
    assert _verdicts(_video("new", age=2), _video("b"), _video("c"), _video("d"))["new"] == "too_early"


def test_impression_starvation_beats_low_data() -> None:
    """The latest video on 2026-11-23: 25 impressions in its first week against a
    1,200-impression neighbour: starved, even though it is far below 1,000 impressions."""
    verdicts = _verdicts(
        _video("retro", age=6 + 1, impressions=25, ctr=12.0, avd_min=20.0, week=25),
        _video("night", impressions=1200, ctr=2.1, avd_min=15.0, week=1100),
        _video("quiet", impressions=400, ctr=5.0, avd_min=12.0, week=300),
        _video("observatory", impressions=500, ctr=6.0, avd_min=6.0, week=400))
    assert verdicts["retro"] == "starved"
    # 1,200 impressions is enough to judge: CTR 2.1% is under 0.7x the others' median
    # (6%) while viewers stay longer than the others' median (12 min).
    assert verdicts["night"] == "packaging"
    assert verdicts["quiet"] == "low_data"


def test_rules_need_three_comparable_videos() -> None:
    assert _verdicts(_video("a"), _video("b"))["a"] == "no_baseline"


def test_packaging_content_and_double_down() -> None:
    base = [_video(n) for n in ("m1", "m2", "m3", "m4")]
    assert _verdicts(*base, _video("pack", ctr=2.0, avd_min=12.0))["pack"] == "packaging"
    assert _verdicts(*base, _video("content", ctr=5.0, avd_min=5.0))["content"] == "content"
    assert _verdicts(*base, _video("star", ctr=8.0, avd_min=12.0))["star"] == "double_down"
    assert _verdicts(*base, _video("fine"))["fine"] == "ok"
    assert _verdicts(*base, _video("few", impressions=600, week=3000))["few"] == "low_data"


def test_first_week_impressions_counts_only_days_0_to_6() -> None:
    rows = tuple(ReachRow(date(2026, 11, d), "UC", "v", 10, Decimal("0.1")) for d in range(17, 27))
    assert first_week_impressions(rows, video_id="v", published=date(2026, 11, 17)) == 70
    assert first_week_impressions(rows, video_id="x", published=date(2026, 11, 17)) is None


class _Version:
    def __init__(self, report_id: str, status: str) -> None:
        self.report_id, self.revision_status = report_id, status


class _Report:
    def __init__(self, rows: tuple[ReachRow, ...]) -> None:
        self.rows = rows


class _Downloader:
    """Two daily reports plus a superseded revision of the first day."""

    def __init__(self, reports: dict[str, tuple[ReachRow, ...]], statuses: dict[str, str]) -> None:
        self.reports, self.statuses, self.fetched = reports, statuses, []

    def list_reach_reports(self, *, job_id: str) -> list[_Version]:
        return [_Version(rid, status) for rid, status in self.statuses.items()]

    def fetch(self, *, job_id: str, report_id: str, ctr_unit: str) -> _Report:
        self.fetched.append(report_id)
        return _Report(self.reports[report_id])


def _row(day: int, video: str = "v", impressions: int = 10, channel: str = "UC") -> ReachRow:
    return ReachRow(date(2026, 11, day), channel, video, impressions, Decimal("0.05"))


def test_daily_report_reads_only_current_revisions() -> None:
    from scripts.channel_daily_report import download_reach_rows

    downloader = _Downloader(
        {"r1": (_row(20),), "r1old": (_row(20, impressions=99),), "r2": (_row(21),)},
        {"r1": "current", "r1old": "superseded", "r2": "current"})
    rows = download_reach_rows(downloader, job_id="j", ctr_unit="ratio", channel_id="UC")  # type: ignore[arg-type]
    assert sorted(r.impressions for r in rows) == [10, 10]
    assert "r1old" not in downloader.fetched
    other = _Downloader({"r": (_row(20, channel="UCx"),)}, {"r": "current"})
    with pytest.raises(ValueError, match="another channel"):
        download_reach_rows(other, job_id="j", ctr_unit="ratio", channel_id="UC")  # type: ignore[arg-type]


def test_report_says_unknown_instead_of_zero() -> None:
    from scripts.channel_daily_report import render

    facts = [_video("a", impressions=None, ctr=None, week=None)]
    text = render(facts, evaluate_channel(facts), today=date(2026, 11, 23), reach_days=0)
    assert "| unknown | unknown | unknown |" in text and "no_baseline" in text


def test_report_escapes_pipes_in_titles() -> None:
    from scripts.channel_daily_report import render

    facts = [VideoFacts("v", "Retro Synth | 104 Minutes", 6, None, None, None, None)]
    text = render(facts, evaluate_channel(facts), today=date(2026, 11, 23), reach_days=0)
    assert r"Retro Synth \| 104 Minutes (v)" in text

"""Evidence-preserving ingestion of official reach CSV; never guess CTR units."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from analytics.ingestion.youtube_reach import (
    ReachReportError,
    parse_reach_csv,
    summarize_reach,
)

HEADER = (
    "date,channel_id,video_id,video_thumbnail_impressions,"
    "video_thumbnail_impressions_ctr\n"
)


def test_impression_weighting_and_window_coverage_not_simple_ctr_average() -> None:
    rows = parse_reach_csv(
        HEADER + "2026-10-01,UC_test,video_a,100,0.1\n"
        + "2026-10-02,UC_test,video_a,900,0.02\n"
        + "2026-10-02,UC_test,video_b,100,0.5\n",
        ctr_unit="ratio",
    )
    summary = summarize_reach(
        rows, video_id="video_a", channel_id="UC_test",
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 3),
    )
    assert summary.observed_days == 2 and summary.window_days == 3
    assert summary.impressions == 1000
    assert summary.impression_weighted_ctr_pct == Decimal("2.8")


def test_percent_unit_is_explicit_and_not_doubled() -> None:
    rows = parse_reach_csv(
        HEADER + "2026-10-03,UC_test,vid,500,4.5\n", ctr_unit="percent"
    )
    value = summarize_reach(
        rows, video_id="vid", channel_id="UC_test",
        start_date=date(2026, 10, 3), end_date=date(2026, 10, 3),
    )
    assert value.impression_weighted_ctr_pct == Decimal("4.500")


def test_absent_window_or_zero_impressions_cannot_infer_ctr() -> None:
    rows = parse_reach_csv(
        HEADER + "2026-10-01,UC_test,vid,0,0\n", ctr_unit="ratio",
    )
    summary = summarize_reach(
        rows, video_id="vid", channel_id="UC_test",
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 2),
    )
    assert summary.impressions == 0 and summary.impression_weighted_ctr_pct is None
    missing = summarize_reach(
        rows, video_id="vid", channel_id="UC_wrong",
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 2),
    )
    assert missing.observed_days == 0 and missing.impression_weighted_ctr_pct is None


@pytest.mark.parametrize("content", [
    "date,video_id,video_thumbnail_impressions\n2026-10-01,vid,500\n",
    HEADER + "2026-10-01,UC_test,vid,100,4.5\n",
    HEADER + "2026-10-01,UC_test,vid,100,NaN\n",
    HEADER + "2026-10-01,UC_test,vid,-1,0.1\n",
    HEADER + "2026-10-01,UC_test,vid,0,0.2\n",
    HEADER + "not-a-date,UC_test,vid,100,0.2\n",
    HEADER + "2026-10-01,UC_test,vid,100,0.2\n"
             + "2026-10-01,UC_test,vid,100,0.2\n",
])
def test_invalid_csv_never_becomes_valid_ctr(content: str) -> None:
    with pytest.raises(ReachReportError):
        parse_reach_csv(content, ctr_unit="ratio")


def test_unit_must_be_selected() -> None:
    with pytest.raises(ReachReportError):
        parse_reach_csv(HEADER, ctr_unit="auto")  # type: ignore[arg-type]


def test_out_of_window_rows_are_not_sum_of_all_snapshots() -> None:
    rows = parse_reach_csv(
        HEADER + "2026-09-30,UC_test,vid,900,0.5\n"
        + "2026-10-01,UC_test,vid,50,0.1\n", ctr_unit="ratio",
    )
    summary = summarize_reach(
        rows, video_id="vid", channel_id="UC_test",
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 1),
    )
    assert summary.impressions == 50
    assert summary.impression_weighted_ctr_pct == Decimal("10.0")

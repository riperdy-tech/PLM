"""Reporting date boundaries follow Pacific daylight saving time."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from analytics.ingestion.youtube_reach import ReachRow
from analytics.ingestion.youtube_reporting import (
    AuthenticatedReachReport,
    ReachReportVersion,
)
from analytics.projection.candidate_performance import (
    CandidateAttributionError,
    preview_authenticated_reach,
)


def _report(start: str, end: str, day: date):
    beginning = datetime.fromisoformat(start)
    ending = datetime.fromisoformat(end)
    created = datetime(2026, 11, 5, tzinfo=UTC)
    report = AuthenticatedReachReport(
        job_id="job_1", report_id="report_1",
        report_type="channel_reach_basic_a1",
        source_kind="google_reporting_api_authenticated_download",
        start_time=beginning, end_time=ending, create_time=created,
        csv_sha256="a" * 64,
        rows=(ReachRow(day, "UC_test", "yt_test_01", 100, Decimal("0.05")),),
    )
    revision = ReachReportVersion(
        job_id="job_1", report_id="report_1",
        start_time=beginning, end_time=ending, create_time=created,
        revision_status="current", superseded_by=None,
    )
    return report, revision


@pytest.mark.parametrize(("start", "end", "report_day"), [
    ("2026-03-08T08:00:00+00:00", "2026-03-09T07:00:00+00:00", date(2026, 3, 8)),
    ("2026-11-01T07:00:00+00:00", "2026-11-02T08:00:00+00:00", date(2026, 11, 1)),
    ("2026-09-10T07:00:00+00:00", "2026-09-11T07:00:00+00:00", date(2026, 9, 10)),
])
def test_pacific_calendar_days_including_dst_are_accepted(
    start: str, end: str, report_day: date,
) -> None:
    report, revision = _report(start, end, report_day)
    preview = preview_authenticated_reach(
        {"published_at": "2026-01-01T00:00:00+00:00",
         "video_id": "yt_test_01", "channel_id": "UC_test"},
        report=report, revision=revision, ctr_unit="ratio",
        start_date=report_day, end_date=report_day, as_of_date=date(2026, 12, 1),
    )
    assert preview["video_reach"]["observed_days"] == 1
    assert preview["video_reach"]["video_thumbnail_impressions"] == 100
    assert preview["video_reach"]["video_thumbnail_ctr_percent"] == "5.00"
    assert preview["video_reach"]["reporting_day_basis"] == (
        "America/Los_Angeles_documented_DST_aware"
    )


@pytest.mark.parametrize(("start", "end", "day"), [
    ("2026-09-10T00:00:00+00:00", "2026-09-11T00:00:00+00:00", date(2026, 9, 10)),
    ("2026-03-08T08:00:00+00:00", "2026-03-09T08:00:00+00:00", date(2026, 3, 8)),
    ("2026-11-01T07:00:00+00:00", "2026-11-02T07:00:00+00:00", date(2026, 11, 1)),
])
def test_wrong_utc_boundary_or_day_length_refused(
    start: str, end: str, day: date,
) -> None:
    report, revision = _report(start, end, day)
    with pytest.raises(CandidateAttributionError, match="Pacific reporting day"):
        preview_authenticated_reach(
            {"published_at": "2026-01-01T00:00:00+00:00",
             "video_id": "yt_test_01", "channel_id": "UC_test"},
            report=report, revision=revision, ctr_unit="ratio",
            start_date=day, end_date=day, as_of_date=date(2026, 12, 1),
        )


def test_publication_day_uses_pacific_local_date_not_utc() -> None:
    report, revision = _report(
        "2026-09-10T07:00:00+00:00",
        "2026-09-11T07:00:00+00:00", date(2026, 9, 10),
    )
    with pytest.raises(CandidateAttributionError, match="publication Pacific day"):
        preview_authenticated_reach(
            {"published_at": "2026-09-11T03:00:00+00:00",
             "video_id": "yt_test_01", "channel_id": "UC_test"},
            report=report, revision=revision, ctr_unit="ratio",
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 10), as_of_date=date(2026, 12, 1),
        )

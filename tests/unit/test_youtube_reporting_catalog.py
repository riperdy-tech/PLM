"""Read-only Google Reporting catalog enumeration and backfill revision safety."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from analytics.ingestion.youtube_reporting import (
    AuthenticatedReachDownloader,
    ReportingDownloadError,
)

from scripts import poll_live_analytics

ROOT = "https://youtubereporting.googleapis.com"
JOB = {"id": "job_1", "reportTypeId": "channel_reach_basic_a1"}


def _report(id: str, start: str, created: str) -> dict[str, str]:
    return {
        "id": id,
        "jobId": "job_1",
        "startTime": start,
        "endTime": ("2026-10-02T00:00:00Z" if start.startswith("2026-10-01")
                    else "2026-10-03T00:00:00Z"),
        "createTime": created,
        "downloadUrl": f"{ROOT}/v1/media/{id}?alt=media",
    }


OLDER = _report("old", "2026-10-01T00:00:00Z", "2026-10-03T10:00:00Z")
NEWER = _report("revised", "2026-10-01T00:00:00Z", "2026-10-04T10:00:00Z")
OTHER = _report("other_day", "2026-10-02T00:00:00Z", "2026-10-03T10:00:00Z")


def _downloader(
    pages: dict[tuple[str, str | None], dict[str, Any]],
    seen: list[tuple[str, str | None]],
) -> AuthenticatedReachDownloader:
    def respond(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert req.headers["Authorization"] == "Bearer test_bearer"
        assert req.url.host == "youtubereporting.googleapis.com"
        assert req.url.params.get("includeSystemManaged") in (None, "true")
        token = req.url.params.get("pageToken")
        path = req.url.path
        seen.append((path, token))
        if path == "/v1/jobs/job_1":
            return httpx.Response(200, json=JOB)
        return httpx.Response(200, json=pages[path, token])

    return AuthenticatedReachDownloader(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        access_token=lambda: "test_bearer",
    )


def test_jobs_discovery_visits_pages_and_filters_without_guessing_a_job() -> None:
    seen: list[tuple[str, str | None]] = []
    pages = {
        ("/v1/jobs", None): {
            "jobs": [
                {"id": "job_2", "reportTypeId": "channel_basic_a2"},
                {"id": "reach_z", "reportTypeId": "channel_reach_basic_a1"},
            ], "nextPageToken": "next",
        },
        ("/v1/jobs", "next"): {
            "jobs": [{"id": "reach_a", "reportTypeId": "channel_reach_basic_a1"}],
        },
    }
    jobs = _downloader(pages, seen).list_reach_jobs()
    assert [j.job_id for j in jobs] == ["reach_a", "reach_z"]
    assert all(j.report_type == "channel_reach_basic_a1" for j in jobs)
    assert seen == [("/v1/jobs", None), ("/v1/jobs", "next")]


def test_report_catalog_preserves_revisions_across_pages() -> None:
    seen: list[tuple[str, str | None]] = []
    pages = {
        ("/v1/jobs/job_1/reports", None): {
            "reports": [OLDER, OTHER], "nextPageToken": "page_two",
        },
        ("/v1/jobs/job_1/reports", "page_two"): {"reports": [NEWER]},
    }
    versions = _downloader(pages, seen).list_reach_reports(job_id="job_1")
    assert [(r.report_id, r.revision_status, r.superseded_by) for r in versions] == [
        ("revised", "current", None),
        ("old", "superseded", "revised"),
        ("other_day", "current", None),
    ]
    assert versions[0].start_time == versions[1].start_time
    assert versions[0].create_time > versions[1].create_time
    assert seen == [
        ("/v1/jobs/job_1", None),
        ("/v1/jobs/job_1/reports", None),
        ("/v1/jobs/job_1/reports", "page_two"),
    ]


def test_older_revision_timestamp_collision_is_also_refused() -> None:
    same_period_older = {
        **OLDER, "id": "old_dup",
        "downloadUrl": f"{ROOT}/v1/media/old_dup?alt=media",
    }
    pages = {
        ("/v1/jobs/job_1/reports", None): {
            "reports": [NEWER, OLDER, same_period_older],
        },
    }
    with pytest.raises(ReportingDownloadError, match="ambiguous"):
        _downloader(pages, []).list_reach_reports(job_id="job_1")


@pytest.mark.parametrize("report_rows,reason", [
    ([OLDER, OLDER], "duplicate report"),
    ([OLDER, {**NEWER, "createTime": OLDER["createTime"]}], "ambiguous"),
    ([{**OLDER, "jobId": "foreign_job"}], "identity mismatch"),
    ([{**OLDER, "startTime": "2026-10-03T00:00:00Z"}], "interval"),
    ([{**OLDER, "downloadUrl": "https://unsafe.invalid/report.csv"}], "approved Google"),
])
def test_bad_report_list_refused_before_any_download(
    report_rows: list[dict[str, str]], reason: str,
) -> None:
    pages = {("/v1/jobs/job_1/reports", None): {"reports": report_rows}}
    seen: list[tuple[str, str | None]] = []
    with pytest.raises(ReportingDownloadError, match=reason):
        _downloader(pages, seen).list_reach_reports(job_id="job_1")
    assert all("/media/" not in path for path, _ in seen)


@pytest.mark.parametrize("next_token", ["loop", 3, "x" * 1100])
def test_catalog_pagination_requires_completion(next_token: Any) -> None:
    pages = {
        ("/v1/jobs", None): {
            "jobs": [{"id": "job_1", "reportTypeId": "channel_reach_basic_a1"}],
            "nextPageToken": next_token,
        },
        ("/v1/jobs", "loop"): {
            "jobs": [], "nextPageToken": "loop",
        },
    }
    with pytest.raises(ReportingDownloadError, match="pagination"):
        _downloader(pages, []).list_reach_jobs()


def test_report_catalog_page_budget_is_fail_closed() -> None:
    pages: dict[tuple[str, str | None], dict[str, Any]] = {}
    for i in range(10):
        pages["/v1/jobs", None if i == 0 else f"p{i}"] = {
            "jobs": [], "nextPageToken": f"p{i + 1}",
        }
    with pytest.raises(ReportingDownloadError, match="pagination"):
        _downloader(pages, []).list_reach_jobs()


def test_catalog_cli_reads_only_analytics_oauth_and_never_polls_uploads(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeCatalog:
        def __init__(self, **kwargs: Any):
            assert callable(kwargs["access_token"])

        def list_reach_jobs(self):
            return (SimpleNamespace(job_id="job_1", report_type="channel_reach_basic_a1"),)

        def list_reach_reports(self, *, job_id: str):
            assert job_id == "job_1"
            return (SimpleNamespace(
                job_id=job_id, report_id="revised",
                start_time=datetime(2026, 10, 1, tzinfo=UTC),
                end_time=datetime(2026, 10, 2, tzinfo=UTC),
                create_time=datetime(2026, 10, 4, tzinfo=UTC),
                revision_status="current", superseded_by=None,
            ),)

    monkeypatch.setattr(
        poll_live_analytics, "RealYouTubeAnalyticsClient",
        lambda: SimpleNamespace(is_configured=True, _get_access_token=lambda: "t"),
    )
    monkeypatch.setattr(poll_live_analytics, "AuthenticatedReachDownloader", FakeCatalog)

    def unexpected_data_client():
        pytest.fail("Catalog listing must not require the YouTube Data API")

    monkeypatch.setattr(poll_live_analytics, "RealYouTubeClient", unexpected_data_client)
    monkeypatch.setattr(
        poll_live_analytics, "poll_recent_uploads",
        lambda *a, **kw: pytest.fail("catalog must not poll videos"),
    )
    with caplog.at_level(logging.INFO):
        assert poll_live_analytics.main(["--list-reach-jobs"]) == 0
        assert poll_live_analytics.main(["--list-reach-reports", "job_1"]) == 0
    assert "Reach job id=job_1" in caplog.text
    assert "revision=current" in caplog.text
    assert "report" in caplog.text


@pytest.mark.parametrize("args", [
    ["--list-reach-jobs", "--list-reach-reports", "job_1"],
    ["--list-reach-jobs", "--reach-report", "x.csv"],
    ["--list-reach-jobs", "--channel-id", "UC_test"],
    ["--list-reach-reports", "job_1", "--reach-job-id", "job_1", "--reach-report-id", "r"],
])
def test_catalog_cli_rejects_combined_modes_before_io(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        poll_live_analytics.main(args)
    assert exc.value.code == 2

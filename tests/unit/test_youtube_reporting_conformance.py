"""R6 step 1: the Reporting downloader against docs-derived Google responses.

Proves the adapter handles the documented response shapes (extra fields,
microsecond timestamps, pagination, backfill revisions, the Google error
envelope) and sends only read-only, authenticated GETs. It does not prove
Google still answers this way; see ``tests/support/provider_fixtures.py``.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from analytics.ingestion.youtube_reporting import (
    AuthenticatedReachDownloader,
    ReportingAccessDenied,
    ReportingDownloadError,
)

from tests.support.http import MockHTTP
from tests.support.provider_fixtures import enqueue, load

P = "youtube_reporting"
JOB_ID = "6a3c9f1e-2b7d-4c8a-9e1f-0d5b7a3c2e10"


def _downloader(mock: MockHTTP) -> AuthenticatedReachDownloader:
    return AuthenticatedReachDownloader(client=mock.client(), access_token=lambda: "tok")


def _assert_read_only(mock: MockHTTP) -> None:
    for request in mock.requests:
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.url.startswith("https://youtubereporting.googleapis.com/v1/")


def test_job_catalog_pages_and_keeps_only_reach_jobs(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "jobs_list_page1", "jobs_list_page2")
    jobs = _downloader(mock_http).list_reach_jobs()
    assert [j.job_id for j in jobs] == [JOB_ID, "c0ffee00-1111-4222-8333-944455556666"]
    assert {j.report_type for j in jobs} == {"channel_reach_basic_a1"}
    mock_http.assert_query_params(0, pageSize="100", includeSystemManaged="true")
    assert mock_http.request(1).param("pageToken") == "CAEQAA"
    _assert_read_only(mock_http)


def test_report_catalog_pages_and_classifies_backfill(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "job_reach", "reports_list_page1", "reports_list_page2")
    versions = _downloader(mock_http).list_reach_reports(job_id=JOB_ID)
    by_id = {v.report_id: v for v in versions}
    assert by_id["9180799901"].revision_status == "current"
    assert by_id["9180734512"].revision_status == "superseded"
    assert by_id["9180734512"].superseded_by == "9180799901"
    assert by_id["9180811234"].revision_status == "current"
    assert by_id["9180799901"].create_time == datetime(2026, 10, 12, 4, 2, 11, 900001,
                                                       tzinfo=UTC)
    assert mock_http.request(2).param("pageToken") == "CGQQAA"
    _assert_read_only(mock_http)


def test_fetch_downloads_the_documented_reach_csv(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "job_reach", "report_get", "reach_csv")
    report = _downloader(mock_http).fetch(job_id=JOB_ID, report_id="9180799901",
                                          ctr_unit="ratio")
    csv = load(P, "reach_csv").text
    assert csv is not None
    assert report.csv_sha256 == hashlib.sha256(csv.encode()).hexdigest()
    assert [(r.video_id, r.impressions) for r in report.rows] == [
        ("lPYmaPHPQr4", 1520), ("aqTf6gzjsNI", 0)]
    assert report.rows[0].date.isoformat() == "2026-10-01"
    assert [r.path for r in mock_http.requests] == [
        f"/v1/jobs/{JOB_ID}", f"/v1/jobs/{JOB_ID}/reports/9180799901",
        "/v1/media/9180799901"]
    _assert_read_only(mock_http)


def test_a_job_of_another_report_type_is_refused(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "job_other_type")
    with pytest.raises(ReportingDownloadError, match="approved channel reach"):
        _downloader(mock_http).fetch(job_id="b7e2d4c1-9f3a-4e6b-8c2d-1a0f9e8d7c65",
                                     report_id="9180799901", ctr_unit="ratio")


@pytest.mark.parametrize("error", ["error_401", "error_403"])
def test_google_auth_errors_are_access_denied(mock_http: MockHTTP, error: str) -> None:
    enqueue(mock_http, P, error)
    with pytest.raises(ReportingAccessDenied):
        _downloader(mock_http).list_reach_jobs()


def test_missing_job_is_a_download_error_not_access_denied(mock_http: MockHTTP) -> None:
    enqueue(mock_http, P, "error_404")
    with pytest.raises(ReportingDownloadError, match="HTTP 404") as raised:
        _downloader(mock_http).list_reach_reports(job_id=JOB_ID)
    assert not isinstance(raised.value, ReportingAccessDenied)


@pytest.mark.parametrize("error", ["error_401", "error_403"])
def test_download_auth_errors_are_access_denied(mock_http: MockHTTP, error: str) -> None:
    enqueue(mock_http, P, "job_reach", "report_get", error)
    with pytest.raises(ReportingAccessDenied, match="download denied"):
        _downloader(mock_http).fetch(job_id=JOB_ID, report_id="9180799901",
                                     ctr_unit="ratio")

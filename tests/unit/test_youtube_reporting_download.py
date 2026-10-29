"""Google Reporting API reach acquisition is read-only and provenance-preserving."""
from __future__ import annotations

import hashlib

import httpx
import pytest
from analytics.ingestion.youtube_reporting import (
    MAX_CSV_BYTES,
    AuthenticatedReachDownloader,
    ReportingDownloadError,
)

ROOT = "https://youtubereporting.googleapis.com"
CSV = (
    b"date,channel_id,video_id,video_thumbnail_impressions,"
    b"video_thumbnail_impressions_ctr\n"
    b"2026-10-01,UC_expected,video_one,400,0.025\n"
)
JOB = {"id": "job_1", "reportTypeId": "channel_reach_basic_a1"}
REPORT = {
    "id": "report_2",
    "jobId": "job_1",
    "startTime": "2026-10-01T00:00:00Z",
    "endTime": "2026-10-02T00:00:00Z",
    "createTime": "2026-10-03T12:00:00Z",
    "downloadUrl": f"{ROOT}/v1/media/report_2?alt=media",
}


def _client(
    *, job: dict | None = None, report: dict | None = None,
    download: bytes = CSV, code: int = 200,
    seen: list[str] | None = None,
) -> AuthenticatedReachDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer sample_token"
        assert request.url.scheme == "https"
        assert request.url.host == "youtubereporting.googleapis.com"
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path == "/v1/jobs/job_1":
            return httpx.Response(200, json=JOB if job is None else job)
        if request.url.path == "/v1/jobs/job_1/reports/report_2":
            return httpx.Response(200, json=REPORT if report is None else report)
        if request.url.path == "/v1/media/report_2":
            return httpx.Response(code, content=download, headers=(
                {"Location": "https://evil.example/steal"} if code == 302 else {}
            ))
        raise AssertionError("unexpected Google Reporting API request")

    return AuthenticatedReachDownloader(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        access_token=lambda: "sample_token",
    )


def test_authenticated_download_validates_job_report_and_exact_bytes() -> None:
    seen: list[str] = []
    result = _client(seen=seen).fetch(
        job_id="job_1", report_id="report_2", ctr_unit="ratio",
    )
    assert seen == ["/v1/jobs/job_1", "/v1/jobs/job_1/reports/report_2",
                    "/v1/media/report_2"]
    assert result.report_type == "channel_reach_basic_a1"
    assert result.source_kind == "google_reporting_api_authenticated_download"
    assert result.csv_sha256 == hashlib.sha256(CSV).hexdigest()
    assert result.create_time.isoformat() == "2026-10-03T12:00:00+00:00"
    assert len(result.rows) == 1
    assert result.rows[0].impressions == 400


@pytest.mark.parametrize(("job", "report", "expected"), [
    ({"id": "job_1", "reportTypeId": "channel_basic_a2"}, None, "approved"),
    ({"id": "wrong", "reportTypeId": "channel_reach_basic_a1"}, None, "approved"),
    (None, {**REPORT, "jobId": "other_job"}, "metadata does not match"),
    (None, {**REPORT, "id": "different"}, "metadata does not match"),
    (None, {**REPORT, "createTime": "2026-10-03"}, "timezone"),
    (None, {**REPORT, "endTime": "2026-09-30T00:00:00Z"}, "interval"),
])
def test_untrustworthy_google_metadata_refused(
    job: dict | None, report: dict | None, expected: str,
) -> None:
    with pytest.raises(ReportingDownloadError, match=expected):
        _client(job=job, report=report).fetch(
            job_id="job_1", report_id="report_2", ctr_unit="ratio",
        )


@pytest.mark.parametrize("url", [
    "http://youtubereporting.googleapis.com/v1/media/report_2",
    "https://evil.example/v1/media/report_2",
    "https://youtubereporting.googleapis.com.evil.example/v1/media/report_2",
    "https://youtubereporting.googleapis.com:444/v1/media/report_2",
    "https://user@youtubereporting.googleapis.com/v1/media/report_2",
    "https://youtubereporting.googleapis.com/v1/media/../jobs/job_1",
    "https://youtubereporting.googleapis.com/v1/media/report_2?redirect=https://evil.example",
    "https://youtubereporting.googleapis.com/v1/media/report_2#fragment",
])
def test_metadata_url_cannot_exfiltrate_bearer(url: str) -> None:
    seen: list[str] = []
    with pytest.raises(ReportingDownloadError, match="approved Google"):
        _client(report={**REPORT, "downloadUrl": url}, seen=seen).fetch(
            job_id="job_1", report_id="report_2", ctr_unit="ratio",
        )
    assert seen == ["/v1/jobs/job_1", "/v1/jobs/job_1/reports/report_2"]


@pytest.mark.parametrize("ident", [
    "a/b", "../bad", "x?alt=media", "", "a" * 201,
])
def test_report_identifier_is_not_a_url_fragment(ident: str) -> None:
    with pytest.raises(ReportingDownloadError, match="identifier"):
        _client().fetch(job_id="job_1", report_id=ident, ctr_unit="ratio")


def test_redirect_and_oversize_downloads_fail_closed() -> None:
    for payload, status, expected in (
        (CSV, 302, "HTTP 302"),
        (b"x" * (MAX_CSV_BYTES + 1), 200, "4 MiB"),
    ):
        with pytest.raises(ReportingDownloadError, match=expected):
            _client(download=payload, code=status).fetch(
                job_id="job_1", report_id="report_2", ctr_unit="ratio",
            )


def test_invalid_csv_never_establishes_reach() -> None:
    with pytest.raises(ValueError):
        _client(download=b"not,a,reach,report\n").fetch(
            job_id="job_1", report_id="report_2", ctr_unit="ratio",
        )


def test_configured_token_required() -> None:
    source = _client()
    source.access_token = lambda: ""
    with pytest.raises(ReportingDownloadError, match="OAuth token"):
        source.fetch(job_id="job_1", report_id="report_2", ctr_unit="ratio")

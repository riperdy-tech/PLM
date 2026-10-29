"""Authenticated Reporting API to P9 preview: report provenance stays video-level."""
from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest
from analytics.ingestion.youtube_client import YouTubeAnalyticsError
from analytics.ingestion.youtube_reporting import AuthenticatedReachDownloader

from tests.unit.test_analytics_candidate_performance import HEADERS, _api, _ingest

ORIGIN = "https://youtubereporting.googleapis.com"
CSV = (
    b"date,channel_id,video_id,video_thumbnail_impressions,"
    b"video_thumbnail_impressions_ctr\n"
    b"2026-09-10,UC_test,yt_test_01,100,0.10\n"
    b"2026-09-10,UC_test,other_video,900,0.02\n"
)
OLD = {
    "id": "old", "jobId": "job_1",
    "startTime": "2026-09-10T07:00:00Z", "endTime": "2026-09-11T07:00:00Z",
    "createTime": "2026-09-15T00:00:00Z",
    "downloadUrl": f"{ORIGIN}/v1/media/old?alt=media",
}
NEW = {
    **OLD, "id": "new", "createTime": "2026-09-20T00:00:00Z",
    "downloadUrl": f"{ORIGIN}/v1/media/new?alt=media",
}


def _google(*, body: bytes = CSV, catalog: list[dict[str, Any]] | None = None,
            served: dict[str, Any] | None = None,
            google_status: int = 200, catalog_status: int = 200,
            calls: list[str] | None = None) -> AuthenticatedReachDownloader:
    items = [OLD, NEW] if catalog is None else catalog

    def respond(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer reporting-test-token"
        assert req.url.host == "youtubereporting.googleapis.com"
        assert req.method == "GET"
        if calls is not None:
            calls.append(req.url.path)
        if req.url.path == "/v1/jobs/job_1":
            return httpx.Response(catalog_status, json={
                "id": "job_1", "reportTypeId": "channel_reach_basic_a1",
            })
        if req.url.path == "/v1/jobs/job_1/reports":
            return httpx.Response(200, json={"reports": items})
        if req.url.path == "/v1/jobs/job_1/reports/old":
            return httpx.Response(200, json=OLD if served is None else served)
        if req.url.path == "/v1/media/old":
            return httpx.Response(google_status, content=body)
        raise AssertionError("unexpected Google API path")

    return AuthenticatedReachDownloader(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        access_token=lambda: "reporting-test-token",
    )


def _preview(client, **overrides: Any) -> httpx.Response:
    return client.post(
        "/analytics/candidate-performance/att_test_01/authenticated-reach-preview",
        json={
            "job_id": "job_1", "report_id": "old", "ctr_unit": "ratio",
            "start_date": "2026-09-10", "end_date": "2026-09-10",
            "window": "28d", **overrides,
        },
        headers=HEADERS,
    )


def test_google_provenance_is_video_level_without_candidate_ctr() -> None:
    calls: list[str] = []
    client = _api(reporting_downloader=_google(calls=calls))
    _ingest(client, day="2026-09-20", views=123)
    response = _preview(client)
    assert response.status_code == 200, response.text
    info = response.json()
    assert info["views"] == 123
    assert info["metric_source"] == "synthetic"
    assert info["candidate_selection"]["thumbnail_id"] == "thumb_b"
    assert info["thumbnail_impressions"] is None
    assert info["thumbnail_ctr_percent"] is None
    reach = info["video_reach"]
    assert reach["source_kind"] == "google_reporting_api_authenticated_download"
    assert reach["source_reference"] == "jobs/job_1/reports/old"
    assert reach["source_sha256"] == hashlib.sha256(CSV).hexdigest()
    assert reach["video_thumbnail_impressions"] == 100
    assert reach["video_thumbnail_ctr_percent"] == "10.00"
    assert reach["coverage_status"] == "complete"
    assert reach["observed_days"] == 1
    assert reach["window_days"] == 1
    assert reach["reporting_day_basis"] == "America/Los_Angeles_documented_DST_aware"
    assert reach["attribution_scope"] == "video_level_only_not_selected_candidate_effect"
    provenance = reach["report_provenance"]
    assert provenance["report_id"] == "old"
    assert provenance["report_created_at"] == "2026-09-15T00:00:00+00:00"
    assert provenance["revision_status"] == "superseded"
    assert provenance["superseded_by"] == "new"
    assert provenance["authenticated_google_download"] is True
    assert provenance["candidate_exposure_verified"] is False
    assert reach["reporting_timezone_verified"] is False
    assert calls == [
        "/v1/jobs/job_1", "/v1/jobs/job_1/reports",
        "/v1/jobs/job_1", "/v1/jobs/job_1/reports/old", "/v1/media/old",
    ]


def test_missing_report_id_does_not_trigger_download() -> None:
    calls: list[str] = []
    response = _preview(_api(reporting_downloader=_google(calls=calls)),
                        report_id="missing")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "reporting_revision_not_found"
    assert not any("/v1/media/" in url for url in calls)


def test_revision_changed_between_catalog_and_download_is_refused() -> None:
    response = _preview(_api(reporting_downloader=_google(
        served={**OLD, "createTime": "2026-09-16T00:00:00Z"},
    )))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "authenticated_reach_evidence_invalid"


@pytest.mark.parametrize("changes", [
    {"start_date": "2026-09-09"},
    {"end_date": "2026-09-13"},
    {"start_date": "2026-09-01"},
    {"end_date": "2026-10-01"},
    {"job_id": "../wrong"},
    {"ctr_unit": "auto"},
])
def test_invalid_request_or_reach_period_refused(changes: dict[str, str]) -> None:
    response = _preview(_api(reporting_downloader=_google()), **changes)
    assert response.status_code == 422


def test_google_download_failure_and_wrong_channel_are_refused() -> None:
    broken = _preview(_api(reporting_downloader=_google(google_status=503)))
    assert broken.status_code == 502
    assert broken.json()["detail"]["code"] == "authenticated_reporting_source_failed"
    wrong_channel = CSV.replace(b"UC_test", b"UC_wrong")
    response = _preview(_api(reporting_downloader=_google(body=wrong_channel)))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "authenticated_reach_evidence_invalid"


def test_authenticated_preview_requires_service_token() -> None:
    client = _api(reporting_downloader=_google())
    response = client.post(
        "/analytics/candidate-performance/att_test_01/authenticated-reach-preview",
        json={"job_id": "job_1", "report_id": "old", "ctr_unit": "ratio",
              "start_date": "2026-09-10", "end_date": "2026-09-12"},
    )
    assert response.status_code == 401


def test_only_report_in_catalog_is_current_without_inventing_revisions() -> None:
    response = _preview(_api(reporting_downloader=_google(catalog=[OLD])))
    assert response.status_code == 200
    revision = response.json()["video_reach"]["report_provenance"]
    assert revision["revision_status"] == "current"
    assert revision["superseded_by"] is None


def test_unconfigured_google_oauth_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "analytics.api.app.RealYouTubeAnalyticsClient",
        lambda: SimpleNamespace(is_configured=False),
    )
    result = _preview(_api())
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "reporting_source_unavailable"


@pytest.mark.parametrize("status,where", [
    (401, "catalog"), (403, "catalog"), (403, "download"),
])
def test_reporting_oauth_or_account_access_denial_is_explicit(
    status: int, where: str,
) -> None:
    source = _google(
        catalog_status=status if where == "catalog" else 200,
        google_status=status if where == "download" else 200,
    )
    response = _preview(_api(reporting_downloader=source))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "google_reporting_access_denied"


def test_google_oauth_refresh_rejected_as_upstream_failure() -> None:
    def fail_token() -> str:
        raise YouTubeAnalyticsError("refresh rejected")

    source = AuthenticatedReachDownloader(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda req: pytest.fail("no request before token refresh")
        )),
        access_token=fail_token,
    )
    result = _preview(_api(reporting_downloader=source))
    assert result.status_code == 502
    assert result.json()["detail"]["code"] == "authenticated_reporting_source_failed"

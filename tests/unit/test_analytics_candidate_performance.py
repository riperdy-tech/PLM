"""Read-only P7 assignment and P9 stored-metric join; no A/B inference."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import httpx
import pytest
from analytics.api.app import create_app
from analytics.ingestion.youtube_reporting import AuthenticatedReachDownloader
from fastapi.testclient import TestClient
from plm_contracts.seams.c6 import YouTubePublicationPayload
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from sqlalchemy.orm import sessionmaker

from tests.support.analytics_db import create_analytics_engine
from tests.support.seams import publication_fields
from tests.unit.test_analytics_api import MINIMAL_ENV

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
HEADERS = {SERVICE_TOKEN_HEADER: "test-analytics-token"}


def _receipt() -> dict[str, Any]:
    publication = YouTubePublicationPayload.build(**publication_fields(
        published_at=PUBLISHED.isoformat(), metadata_version="meta_b",
    )).to_wire()
    return {
        "attempt_id": "att_test_01", "channel_id": "UC_test", "state": "verified",
        "youtube_video_id": "yt_test_01", "thumbnail_uploaded": True,
        "publication_event": publication,
        "candidate_selection": {
            "policy_version": "p7-candidate-assignment-v1", "method": "explicit",
            "metadata_variant_id": "meta_b", "thumbnail_id": "thumb_b",
            "eligible_metadata_ids": ["meta_a", "meta_b"],
            "eligible_thumbnail_ids": ["thumb_a", "thumb_b"],
        },
    }


def _api(receipt: dict[str, Any] | None = None, *,
         p7_status: int = 200,
         reporting_downloader: AuthenticatedReachDownloader | None = None) -> TestClient:
    payload = _receipt() if receipt is None else receipt
    engine = create_analytics_engine(static_pool=True)
    factory = sessionmaker(bind=engine)

    def p7_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/youtube/attempts/att_test_01"
        assert request.headers[SERVICE_TOKEN_HEADER] == "test-analytics-token"
        return httpx.Response(p7_status, json=payload)

    source = httpx.Client(
        base_url="http://youtube.test", transport=httpx.MockTransport(p7_handler),
    )
    app = create_app(
        session_factory=lambda: factory(), settings=load_settings(env=MINIMAL_ENV),
        clock=FixedClock(NOW), youtube_client=source,
        reporting_downloader=reporting_downloader,
    )
    return TestClient(app)


def _ingest(client: TestClient, *, day: str, views: int | None,
            source: str = "synthetic", channel: str = "UC_test",
            window: str = "28d") -> None:
    response = client.post("/analytics/ingest/youtube", json={
        "video_id": "yt_test_01", "channel_id": channel,
        "date_str": day, "window": window,
        "observed_at": day + "T23:00:00+00:00",
        "views": views, "watch_time_minutes": 70.0,
        "source": source,
    }, headers=HEADERS)
    assert response.status_code == 201, response.text


def _report(client: TestClient, *, window: str = "28d") -> httpx.Response:
    return client.get(
        "/analytics/candidate-performance/att_test_01",
        params={"window": window}, headers=HEADERS,
    )


def test_assignment_joins_latest_single_window_snapshot_without_ctr_guess() -> None:
    client = _api()
    _ingest(client, day="2026-09-20", views=40)
    _ingest(client, day="2026-09-24", views=68)
    _ingest(client, day="2026-09-25", views=800, window="7d")
    report = _report(client)
    assert report.status_code == 200, report.text
    info = report.json()
    assert info["candidate_selection"]["thumbnail_id"] == "thumb_b"
    assert info["candidate_selection"]["metadata_variant_id"] == "meta_b"
    assert info["assignment_status"] == "p7_thumbnail_upload_recorded"
    assert info["metric_status"] == "observed"
    assert info["metric_source"] == "synthetic"
    assert info["views"] == 68  # Not 40 + 68, not the 7d snapshot's 800
    assert info["watch_time_minutes"] == 70.0
    assert info["thumbnail_impressions"] is None
    assert info["thumbnail_ctr_percent"] is None
    assert info["inference"] == "not_an_ab_test"


def test_no_metric_or_legacy_selection_preserves_unknown() -> None:
    receipt = _receipt()
    receipt["candidate_selection"] = None
    receipt["thumbnail_uploaded"] = False
    client = _api(receipt)
    report = _report(client)
    assert report.status_code == 200, report.text
    payload = report.json()
    assert payload["assignment_status"] == "unknown_legacy"
    assert payload["candidate_selection"] is None
    assert payload["metric_status"] == "unavailable"
    assert payload["views"] is None
    assert payload["metric_source"] is None


def test_an_unconfirmed_upload_is_not_exposure_evidence() -> None:
    receipt = _receipt()
    receipt["thumbnail_uploaded"] = False
    client = _api(receipt)
    report = _report(client)
    assert report.status_code == 200
    assert report.json()["assignment_status"] == "p7_thumbnail_upload_unconfirmed"


@pytest.mark.parametrize(("field", "replacement"), [
    ("attempt_id", "another_attempt"),
    ("channel_id", "UC_wrong"),
    ("youtube_video_id", "another_video"),
    ("state", "intent_recorded"),
    ("publication_event", None),
])
def test_publication_receipt_mismatch_refused(field: str, replacement: Any) -> None:
    receipt = _receipt()
    receipt[field] = replacement
    report = _report(_api(receipt))
    assert report.status_code == 409
    assert report.json()["detail"]["code"] == "candidate_attribution_unverified"


def test_wrong_metadata_assignment_or_metric_channel_refused() -> None:
    receipt = _receipt()
    receipt["candidate_selection"]["metadata_variant_id"] = "meta_a"
    assert _report(_api(receipt)).status_code == 409

    client = _api()
    _ingest(client, day="2026-09-20", views=10, channel="UC_different")
    res = _report(client)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "candidate_attribution_unverified"


@pytest.mark.parametrize(("p7_status", "expected"), [
    (404, 404), (401, 502), (503, 502),
])
def test_upstream_p7_refusal_not_a_fake_empty_report(p7_status: int, expected: int) -> None:
    assert _report(_api(p7_status=p7_status)).status_code == expected


REACH_HEADER = (
    "date,channel_id,video_id,video_thumbnail_impressions,"
    "video_thumbnail_impressions_ctr\n"
)


def _preview(client: TestClient, csv: str, **overrides: Any) -> httpx.Response:
    request = {
        "reach_csv": csv, "report_type": "channel_reach_basic_a1",
        "ctr_unit": "ratio", "source_reference": "operator/report-2026-09",
        "start_date": "2026-09-10", "end_date": "2026-09-12",
        "window": "28d", **overrides,
    }
    return client.post(
        "/analytics/candidate-performance/att_test_01/reach-preview",
        json=request, headers=HEADERS,
    )


def test_reach_preview_is_video_level_only_with_partial_coverage() -> None:
    client = _api()
    _ingest(client, day="2026-09-20", views=42)
    csv = (REACH_HEADER
           + "2026-09-10,UC_test,yt_test_01,100,0.10\n"
           + "2026-09-11,UC_test,yt_test_01,900,0.02\n"
           + "2026-09-11,UC_test,other_video,750,0.7\n")
    result = _preview(client, csv)
    assert result.status_code == 200, result.text
    report = result.json()
    assert report["candidate_selection"]["thumbnail_id"] == "thumb_b"
    assert report["views"] == 42
    assert report["thumbnail_impressions"] is None
    assert report["thumbnail_ctr_percent"] is None
    reach = report["video_reach"]
    assert reach["video_thumbnail_impressions"] == 1000
    assert reach["video_thumbnail_ctr_percent"] == "2.80"
    assert reach["observed_days"] == 2
    assert reach["window_days"] == 3
    assert reach["coverage_status"] == "partial"
    assert reach["source_sha256"] == sha256(csv.encode("utf-8")).hexdigest()
    assert reach["source_kind"] == "operator_supplied_csv_unverified"
    assert reach["attribution_scope"] == "video_level_only_not_selected_candidate_effect"
    assert reach["reporting_timezone_verified"] is False


def test_complete_report_and_percent_unit_are_preserved() -> None:
    csv = (REACH_HEADER
           + "2026-09-10,UC_test,yt_test_01,50,4.0\n"
           + "2026-09-11,UC_test,yt_test_01,50,6.0\n")
    result = _preview(
        _api(), csv, ctr_unit="percent", end_date="2026-09-11",
    )
    assert result.status_code == 200, result.text
    reach = result.json()["video_reach"]
    assert reach["coverage_status"] == "complete"
    assert reach["video_thumbnail_impressions"] == 100
    assert reach["video_thumbnail_ctr_percent"] == "5.00"
    assert reach["ctr_unit_in_csv"] == "percent"


def test_missing_video_or_zero_impressions_do_not_become_ctr_zero() -> None:
    missing = _preview(
        _api(), REACH_HEADER + "2026-09-10,UC_test,other_video,123,0.3\n",
    )
    assert missing.status_code == 200
    reach = missing.json()["video_reach"]
    assert reach["coverage_status"] == "unavailable"
    assert reach["observed_days"] == 0
    assert reach["video_thumbnail_impressions"] is None
    assert reach["video_thumbnail_ctr_percent"] is None

    zero = _preview(
        _api(), REACH_HEADER + "2026-09-10,UC_test,yt_test_01,0,0\n",
    )
    assert zero.status_code == 200
    reach = zero.json()["video_reach"]
    assert reach["video_thumbnail_impressions"] == 0
    assert reach["video_thumbnail_ctr_percent"] is None


def test_legacy_assignment_remains_unknown_with_video_level_reach() -> None:
    receipt = _receipt()
    receipt["candidate_selection"] = None
    client = _api(receipt)
    result = _preview(
        client, REACH_HEADER + "2026-09-10,UC_test,yt_test_01,100,0.08\n",
    )
    assert result.status_code == 200
    assert result.json()["candidate_selection"] is None
    assert result.json()["assignment_status"] == "unknown_legacy"
    assert result.json()["thumbnail_ctr_percent"] is None
    assert result.json()["video_reach"]["video_thumbnail_ctr_percent"] == "8.00"


@pytest.mark.parametrize(("raw", "overrides"), [
    (REACH_HEADER + "2026-09-10,UC_wrong,yt_test_01,100,0.1\n", {}),
    (REACH_HEADER + "2026-09-10,UC_test,yt_test_01,100,0.1\n"
     + "2026-09-10,UC_test,yt_test_01,200,0.2\n", {}),
    (REACH_HEADER + "2026-09-10,UC_test,yt_test_01,100,6.0\n", {}),
    (REACH_HEADER + "2026-10-01,UC_test,yt_test_01,100,0.1\n", {}),
    (REACH_HEADER + "2026-09-10,UC_test,yt_test_01,100,0.1\n",
     {"start_date": "2026-09-01"}),
    (REACH_HEADER + "2026-09-10,UC_test,yt_test_01,100,0.1\n",
     {"end_date": "2026-10-01"}),
])
def test_invalid_reach_evidence_is_refused(raw: str, overrides: dict[str, Any]) -> None:
    response = _preview(_api(), raw, **overrides)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "reach_evidence_invalid"


def test_preview_requires_explicit_source_unit_and_report_identity() -> None:
    csv = REACH_HEADER + "2026-09-10,UC_test,yt_test_01,100,0.10\n"
    for extra in (
        {"ctr_unit": "auto"},
        {"source_reference": "   "},
        {"report_type": "channel_basic_a2"},
        {"start_date": "2026-09-12", "end_date": "2026-09-10"},
    ):
        response = _preview(_api(), csv, **extra)
        assert response.status_code == 422


def test_p9_endpoint_requires_service_token() -> None:
    client = _api()
    report = client.get("/analytics/candidate-performance/att_test_01")
    assert report.status_code == 401

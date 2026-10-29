"""Read-only P7 plus Google report catalog preflight, no CSV transfer."""
from __future__ import annotations

from typing import Any

import pytest

from tests.unit.test_analytics_authenticated_reporting import OLD, _google
from tests.unit.test_analytics_candidate_performance import HEADERS, _api


def _ready(**overrides: Any):
    calls: list[str] = []
    client = _api(reporting_downloader=_google(
        calls=calls,
        catalog_status=overrides.pop("catalog_status", 200),
        google_status=overrides.pop("google_status", 200),
        catalog=overrides.pop("catalog", None),
    ))
    response = client.post(
        "/analytics/candidate-performance/att_test_01/reporting-readiness",
        json={"job_id": "job_1", "report_id": "old",
              "start_date": "2026-09-10", "end_date": "2026-09-10",
              **overrides}, headers=HEADERS,
    )
    return response, calls

def test_catalog_only_preflight_never_downloads_or_invents_metrics() -> None:
    result, calls = _ready(google_status=403)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "catalog_metadata_eligible_only"
    assert data["candidate_selection"]["thumbnail_id"] == "thumb_b"
    assert data["report"]["revision_status"] == "superseded"
    assert data["report"]["superseded_by"] == "new"
    assert data["checks"]["google_job_and_report"] == "read_from_authenticated_catalog"
    assert data["checks"]["google_download_access"] == "not_checked"
    assert data["checks"]["csv_integrity_and_channel"] == "not_checked"
    assert data["checks"]["channel_ownership"] == "not_independently_verified"
    assert data["checks"]["thumbnail_exposure"] == "not_verified"
    assert data["report_metrics"] is None
    assert data["download_performed"] is False
    assert calls == ["/v1/jobs/job_1", "/v1/jobs/job_1/reports"]


@pytest.mark.parametrize(("change", "expected"), [
    ({"report_id": "missing"}, 404),
    ({"start_date": "2026-09-09"}, 422),
    ({"end_date": "2026-09-11"}, 422),
    ({"job_id": "../escape"}, 422),
    ({"ctr_unit": "ratio"}, 422),
    ({"catalog_status": 403}, 403),
    ({"catalog_status": 503}, 502),
])
def test_unavailable_catalog_or_ineligible_report_refused(
    change: dict[str, Any], expected: int,
) -> None:
    response, calls = _ready(**change)
    assert response.status_code == expected
    assert all("/v1/media/" not in path for path in calls)


def test_wrong_pacific_day_not_ready() -> None:
    response, calls = _ready(
        catalog=[{**OLD, "startTime": "2026-09-10T00:00:00Z"}],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "reporting_readiness_ineligible"
    assert all("/v1/media/" not in path for path in calls)


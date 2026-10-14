"""Unit tests for FastAPI `/distribution` endpoints (Phase P8)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from distribution.api.app import create_app
from distribution.client.mock import MockDistributorClient
from distribution.engine import DistributionEngine
from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from tests.support.clearance import p5_holding
from tests.support.distribution_db import create_distribution_engine
from tests.support.seams import media_package, release_manifest

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "test-dist-token",
}


@pytest.fixture
def client() -> TestClient:
    engine = create_distribution_engine(static_pool=True)

    def session_factory():
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=engine)()

    settings = load_settings(env=MINIMAL_ENV)
    clock = FixedClock(NOW)
    dist_mock = MockDistributorClient(clock=clock)
    dist_engine = DistributionEngine(dist_mock)
    # P5, the authority, holds a cleared manifest for cand_city and a quarantined one for
    # cand_q; whatever a request claims, P8 re-asks it before writing.
    p5 = p5_holding(
        release_manifest(candidate_id="cand_city"),
        release_manifest(candidate_id="cand_q", disposition="QUARANTINED"),
    )
    app = create_app(
        session_factory=session_factory, settings=settings, engine=dist_engine, clock=clock,
        clearance=p5,
    )
    return TestClient(app)


def test_health_endpoint_is_unauthenticated(client: TestClient) -> None:
    res = client.get("/distribution/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"
    assert res.json()["service"] == "distribution"
    assert res.json()["timestamp"] == NOW.isoformat()


def test_submit_rejects_unauthenticated(client: TestClient) -> None:
    res = client.post("/distribution/releases", json={})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "invalid_service_token"


def test_release_submission_and_lifecycle_roundtrip(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-dist-token"}
    payload = {
        "track_manifests": [release_manifest(candidate_id="cand_city").to_wire()],
        "media_package": media_package("cand_city").to_wire(),
        "publication": None,
        "artist_name": "API Neon Artist",
        "release_title": "City Skylines",
        "release_date": "2026-10-01",
        "label_name": "Neon Records",
        "copyright_line": "2026 Neon Records",
        "publisher_line": "2026 Neon Music Publishing",
        "territories": "WORLDWIDE",
    }

    # 1. Submit release
    res_sub = client.post("/distribution/releases", json=payload, headers=headers)
    assert res_sub.status_code == 201, res_sub.text
    data = res_sub.json()
    assert data["status"] == "submitted"
    assert data["release_title"] == "City Skylines"
    assert data["provider"] == "mock_distributor"
    assert data["created_at"] == NOW.isoformat()
    rel_id = data["release_id"]

    # 2. Get release
    res_get = client.get(f"/distribution/releases/{rel_id}", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["release_id"] == rel_id

    # 3. Reconcile release status
    res_rec = client.post(f"/distribution/releases/{rel_id}/reconcile", headers=headers)
    assert res_rec.status_code == 200
    assert res_rec.json()["status"] == "delivered"

    # 4. Request takedown
    res_td = client.post(f"/distribution/releases/{rel_id}/takedown", headers=headers)
    assert res_td.status_code == 200
    assert res_td.json()["status"] == "taken_down"


def test_reports_api_roundtrip(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-dist-token"}
    payload = {
        "provider": "apple_music",
        "provider_period": "2026-09",
        "raw_content": "statement_csv_data",
        "source_reference": "apple_sept_2026.csv",
    }

    res_ingest = client.post("/distribution/reports", json=payload, headers=headers)
    assert res_ingest.status_code == 201, res_ingest.text
    data = res_ingest.json()
    assert data["provider"] == "apple_music"
    assert data["received_at"] == NOW.isoformat()
    rep_id = data["report_id"]

    res_get = client.get(f"/distribution/reports/{rep_id}", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["report_id"] == rep_id


def test_release_submission_refuses_an_uncleared_track(client) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-dist-token"}
    payload = {
        "track_manifests": [
            release_manifest(candidate_id="cand_q", disposition="QUARANTINED").to_wire()
        ],
        "media_package": media_package("cand_q").to_wire(),
        "artist_name": "API Neon Artist",
        "release_date": "2026-10-01",
        "label_name": "Neon Records",
        "copyright_line": "2026 Neon Records",
        "publisher_line": "2026 Neon Music Publishing",
        "territories": "WORLDWIDE",
    }
    res = client.post("/distribution/releases", json=payload, headers=headers)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "release_not_cleared"


def test_a_request_claiming_clearance_p5_never_gave_is_refused(client) -> None:
    """Proves: the manifest on the request is a claim. It says PASSED, P5 says
    QUARANTINED, and nothing is submitted (alternate path into P8 is closed)."""
    headers = {SERVICE_TOKEN_HEADER: "test-dist-token"}
    payload = {
        "track_manifests": [release_manifest(candidate_id="cand_q").to_wire()],
        "media_package": media_package("cand_q").to_wire(),
        "artist_name": "API Neon Artist", "release_date": "2026-10-01",
        "label_name": "Neon Records", "copyright_line": "2026 Neon Records",
        "publisher_line": "2026 Neon Music Publishing", "territories": "WORLDWIDE",
    }
    res = client.post("/distribution/releases", json=payload, headers=headers)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "release_not_cleared"
    assert "clearance_not_cleared" in detail["message"]

"""Unit tests for FastAPI `/distribution` endpoints (Phase P8)."""

from __future__ import annotations

import pytest
from distribution.api.app import create_app
from distribution.client.mock import MockDistributorClient
from distribution.engine import DistributionEngine
from fastapi.testclient import TestClient
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from tests.support.distribution_db import create_distribution_engine

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
    dist_mock = MockDistributorClient()
    dist_engine = DistributionEngine(dist_mock)
    app = create_app(session_factory=session_factory, settings=settings, engine=dist_engine)
    return TestClient(app)


def test_health_endpoint_is_unauthenticated(client: TestClient) -> None:
    res = client.get("/distribution/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"
    assert res.json()["service"] == "distribution"


def test_submit_rejects_unauthenticated(client: TestClient) -> None:
    res = client.post("/distribution/releases", json={})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "invalid_service_token"


def test_release_submission_and_lifecycle_roundtrip(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-dist-token"}
    payload = {
        "p5_manifest_id": "p5_api_001",
        "p6_media_package_id": "p6_api_001",
        "artist_name": "API Neon Artist",
        "brand_id": "brand_neon",
        "release_title": "City Skylines",
        "tracks": [
            {
                "title": "City Skylines",
                "duration_seconds": 180.0,
                "audio_uri": "media/audio/city.wav",
                "audio_checksum": "chk_audio_city",
            }
        ],
        "experiment_id": "exp_api_01",
        "experiment_family_id": "fam_api_01",
        "content_arm_id": "arm_api_01",
        "content_genome_id": "gen_api_01",
        "production_directive_id": "dir_api_01",
        "media_package_id": "p6_api_001",
    }

    # 1. Submit release
    res_sub = client.post("/distribution/releases", json=payload, headers=headers)
    assert res_sub.status_code == 201, res_sub.text
    data = res_sub.json()
    assert data["status"] == "submitted"
    assert data["release_title"] == "City Skylines"
    assert data["provider"] == "mock_distributor"
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
    rep_id = data["report_id"]

    res_get = client.get(f"/distribution/reports/{rep_id}", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["report_id"] == rep_id

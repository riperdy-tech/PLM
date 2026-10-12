"""Unit tests for FastAPI `/youtube` endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from youtube_publishing.api.app import create_app
from youtube_publishing.client.mock import MockYouTubeClient

from tests.support.youtube_db import create_youtube_engine

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "test-secret-token",
}


@pytest.fixture
def client() -> TestClient:
    engine = create_youtube_engine(static_pool=True)

    def session_factory():
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=engine)()

    settings = load_settings(env=MINIMAL_ENV)
    yt_mock = MockYouTubeClient()
    app = create_app(session_factory=session_factory, settings=settings, client=yt_mock)
    return TestClient(app)


def test_health_endpoint_is_unauthenticated(client: TestClient) -> None:
    res = client.get("/youtube/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_publish_endpoint_rejects_unauthenticated_request(client: TestClient) -> None:
    res = client.post("/youtube/publish", json={"media_package": {}})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "invalid_service_token"


def test_publish_and_status_roundtrip(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-secret-token"}
    payload = {
        "channel_id": "UC_neon_api_test",
        "operating_mode": "dry_run",
        "media_package": {
            "media_package_id": "pkg_api_test",
            "release_manifest_id": "rm_api_01",
            "experiment_id": "exp_api_01",
            "experiment_family_id": "fam_api_01",
            "content_arm_id": "arm_api_01",
            "content_genome_id": "gen_api_01",
            "production_directive_id": "dir_api_01",
            "brand_id": "brand_neon",
            "video": {
                "master_uri": "media/video/api_test.mp4",
                "checksum": "chk_api_video",
                "duration_seconds": 150.0,
            },
            "thumbnails": [
                {"id": "thumb_01", "uri": "media/thumb/01.jpg"},
            ],
            "metadata_candidates": [
                {
                    "title": "API Test Lofi Beats",
                    "description": "API Test description.",
                    "tags": ["lofi", "api"],
                }
            ],
        },
    }

    res_pub = client.post("/youtube/publish", json=payload, headers=headers)
    assert res_pub.status_code == 200, res_pub.text
    data = res_pub.json()
    assert data["state"] == "verified"
    assert data["youtube_video_id"] is not None
    attempt_id = data["attempt_id"]

    # Check attempt status
    res_att = client.get(f"/youtube/attempts/{attempt_id}", headers=headers)
    assert res_att.status_code == 200
    att_data = res_att.json()
    assert att_data["state"] == "verified"
    assert att_data["youtube_video_id"] == data["youtube_video_id"]

    # Reconcile attempt
    res_rec = client.post(f"/youtube/reconcile/{attempt_id}", headers=headers)
    assert res_rec.status_code == 200
    rec_data = res_rec.json()
    assert rec_data["youtube_video_id"] == data["youtube_video_id"]

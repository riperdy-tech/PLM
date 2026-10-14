"""Unit tests for FastAPI `/youtube` endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from youtube_publishing.api.app import create_app
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.models import ChannelProfile
from youtube_publishing.storage.repositories import insert_channel_profile

from tests.support.seams import media_package
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

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def client() -> TestClient:
    engine = create_youtube_engine(static_pool=True)

    def session_factory():
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=engine)()

    # Channels are configured by an operator (P0 §10); the publish route never invents one.
    with session_factory() as session:
        insert_channel_profile(
            session,
            ChannelProfile(
                id="prof_UC_neon_api_test",
                channel_id="UC_neon_api_test",
                channel_name="Neon API Test",
                brand_id="brand_neon",
                oauth_credential_ref="sec_oauth_test",
                created_at=T0,
                updated_at=T0,
            ),
        )
        session.commit()

    settings = load_settings(env=MINIMAL_ENV)
    yt_mock = MockYouTubeClient()
    app = create_app(
        session_factory=session_factory,
        settings=settings,
        client=yt_mock,
        clock=FixedClock(T0),
    )
    return TestClient(app)


def test_health_endpoint_is_unauthenticated(client: TestClient) -> None:
    res = client.get("/youtube/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"
    assert datetime.fromisoformat(res.json()["timestamp"]) == T0


def test_publish_endpoint_rejects_unauthenticated_request(client: TestClient) -> None:
    res = client.post("/youtube/publish", json={"media_package": {}})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "invalid_service_token"


def test_publish_and_status_roundtrip(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-secret-token"}
    payload = {
        "channel_id": "UC_neon_api_test",
        "operating_mode": "dry_run",
        "media_package": media_package(
            "cand_api_01", media_package_id="pkg_api_test"
        ).to_wire(),
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
    assert datetime.fromisoformat(att_data["completed_at"]) == T0

    # Reconcile attempt
    res_rec = client.post(f"/youtube/reconcile/{attempt_id}", headers=headers)
    assert res_rec.status_code == 200
    rec_data = res_rec.json()
    assert rec_data["youtube_video_id"] == data["youtube_video_id"]


def test_publish_refuses_an_unconfigured_channel(client: TestClient) -> None:
    res = client.post(
        "/youtube/publish",
        json={"channel_id": "UC_unknown", "media_package": media_package().to_wire()},
        headers={SERVICE_TOKEN_HEADER: "test-secret-token"},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "channel_not_configured"


def test_publish_refuses_a_package_with_placeholder_identity(client: TestClient) -> None:
    pkg = media_package().to_wire()
    pkg["lineage"]["content_arm_id"] = "arm_unknown"
    res = client.post(
        "/youtube/publish",
        json={"channel_id": "UC_neon_api_test", "media_package": pkg},
        headers={SERVICE_TOKEN_HEADER: "test-secret-token"},
    )
    assert res.status_code == 422
    assert "media_package.lineage.content_arm_id" in res.json()["detail"]["field_paths"]


def test_publish_refuses_a_naive_schedule(client: TestClient) -> None:
    res = client.post(
        "/youtube/publish",
        json={
            "channel_id": "UC_neon_api_test",
            "media_package": media_package().to_wire(),
            "publish_at": "2030-01-01T12:00:00",
        },
        headers={SERVICE_TOKEN_HEADER: "test-secret-token"},
    )
    assert res.status_code == 422

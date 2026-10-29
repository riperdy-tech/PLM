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

from tests.support.clearance import p5_holding
from tests.support.seams import media_package, release_manifest
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
        clearance=p5_holding(release_manifest(candidate_id="cand_api_01")),
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
    assert att_data["thumbnail_uploaded"] is True
    assert att_data["youtube_video_id"] == data["youtube_video_id"]
    assert datetime.fromisoformat(att_data["completed_at"]) == T0

    # Reconcile attempt
    res_rec = client.post(f"/youtube/reconcile/{attempt_id}", headers=headers)
    assert res_rec.status_code == 200
    rec_data = res_rec.json()
    assert rec_data["youtube_video_id"] == data["youtube_video_id"]


def _multi_candidate_package() -> dict:
    package = media_package(
        "cand_api_01", media_package_id="pkg_candidate_assignment_test",
    ).to_wire()
    package["thumbnails"].append({
        **package["thumbnails"][0], "id": "thumb_b",
        "uri": "media/test/thumb_b.jpg", "checksum": "f" * 64,
    })
    package["metadata_candidates"].append({
        **package["metadata_candidates"][0], "variant_id": "meta_b",
        "title": "Alternate Test Mix",
    })
    return package


def test_explicit_candidate_assignment_replays_without_switching(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-secret-token"}
    request = {
        "channel_id": "UC_neon_api_test", "operating_mode": "dry_run",
        "media_package": _multi_candidate_package(),
        "metadata_variant_id": "meta_b", "thumbnail_id": "thumb_b",
    }
    first = client.post("/youtube/publish", json=request, headers=headers)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["state"] == "verified"
    selection = data["candidate_selection"]
    assert selection["method"] == "explicit"
    assert selection["policy_version"] == "p7-candidate-assignment-v1"
    assert selection["metadata_variant_id"] == "meta_b"
    assert selection["thumbnail_id"] == "thumb_b"
    assert selection["eligible_metadata_ids"] == ["meta_a", "meta_b"]
    assert selection["eligible_thumbnail_ids"] == ["thumb_a", "thumb_b"]

    retry = client.post("/youtube/publish", json=request, headers=headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["manifest_id"] == data["manifest_id"]
    assert retry.json()["candidate_selection"] == selection

    conflict = client.post(
        "/youtube/publish",
        json={**request, "thumbnail_id": "thumb_a"}, headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "candidate_selection_conflict"

    no_selection = client.post(
        "/youtube/publish",
        json={k: v for k, v in request.items()
              if k not in ("metadata_variant_id", "thumbnail_id")}, headers=headers,
    )
    assert no_selection.status_code == 409
    assert no_selection.json()["detail"]["code"] == "candidate_selection_conflict"

    status = client.get(
        f"/youtube/attempts/{data['attempt_id']}", headers=headers,
    )
    assert status.status_code == 200
    assert status.json()["candidate_selection"] == selection


def test_candidate_selection_rejects_missing_or_duplicate_ids(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-secret-token"}
    package = _multi_candidate_package()
    base = {"channel_id": "UC_neon_api_test", "media_package": package}
    missing = client.post(
        "/youtube/publish",
        json={**base, "thumbnail_id": "not_a_candidate"}, headers=headers,
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "candidate_selection_invalid"

    dup_package = _multi_candidate_package()
    dup_package["thumbnails"][1]["id"] = "thumb_a"
    duplicate = client.post(
        "/youtube/publish",
        json={**base, "media_package": dup_package}, headers=headers,
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "candidate_selection_invalid"


def test_default_candidate_selection_records_first_declared(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-secret-token"}
    data = client.post(
        "/youtube/publish",
        json={"channel_id": "UC_neon_api_test", "media_package": _multi_candidate_package()},
        headers=headers,
    )
    assert data.status_code == 200, data.text
    selection = data.json()["candidate_selection"]
    assert selection["method"] == "first_declared"
    assert selection["metadata_variant_id"] == "meta_a"
    assert selection["thumbnail_id"] == "thumb_a"


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

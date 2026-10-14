"""Unit tests for Media Factory FastAPI application and service token auth."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from media_factory.api.app import create_app
from media_factory.config import MediaFactorySettings
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from tests.support.media_db import create_media_engine, media_session
from tests.support.seams import release_manifest

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
}
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_media_api_health_endpoint() -> None:
    settings = MediaFactorySettings()
    app = create_app(session_factory=None, settings=settings, clock=FixedClock(NOW))
    client = TestClient(app)

    response = client.get("/media/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "media_factory"}


def test_media_api_auth_enforcement() -> None:
    # 1. No service token configured -> 503
    platform_no_token = load_settings(env=MINIMAL_ENV)
    settings_no_token = MediaFactorySettings(platform=platform_no_token)
    app_no_token = create_app(session_factory=None, settings=settings_no_token, clock=FixedClock(NOW))
    client = TestClient(app_no_token)

    resp = client.get("/media/packages/mp_123")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "blocked_pending_credentials"

    # 2. Configured token, missing or wrong header -> 401
    platform_with_token = load_settings(
        env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": "super-secret-media-token"}
    )
    settings_with_token = MediaFactorySettings(platform=platform_with_token)
    engine = create_media_engine(static_pool=True)
    factory = lambda: media_session(engine)
    app = create_app(session_factory=factory, settings=settings_with_token, clock=FixedClock(NOW))
    client = TestClient(app)

    resp_no_hdr = client.get("/media/packages/mp_123")
    assert resp_no_hdr.status_code == 401
    assert resp_no_hdr.json()["detail"]["code"] == "invalid_service_token"

    resp_wrong_hdr = client.get(
        "/media/packages/mp_123",
        headers={SERVICE_TOKEN_HEADER: "wrong-token"},
    )
    assert resp_wrong_hdr.status_code == 401
    assert resp_wrong_hdr.json()["detail"]["code"] == "invalid_service_token"


def test_media_api_assemble_and_query_package() -> None:
    token = "test-token"
    platform = load_settings(env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": token})
    settings = MediaFactorySettings(platform=platform)
    engine = create_media_engine(static_pool=True)
    factory = lambda: media_session(engine)
    app = create_app(session_factory=factory, settings=settings, clock=FixedClock(NOW))
    client = TestClient(app)

    headers = {SERVICE_TOKEN_HEADER: token}

    assemble_payload = {
        "track_manifests": [
            release_manifest(candidate_id="song_api_01", measured_duration_seconds=120.0).to_wire(),
            release_manifest(candidate_id="song_api_02", measured_duration_seconds=150.0).to_wire(),
        ],
        "brand_id": "brand_neon",
        "brand_name": "Neon Records",
        "required_duration_seconds": 270.0,
        "visual_profile": "brand_neon_rain_v1",
        "playlist_concept": "Deep Focus",
        "render_profile": "yt_longform_1080p_v1",
    }

    # 1. Assemble package
    resp_assemble = client.post("/media/packages/assemble", json=assemble_payload, headers=headers)
    assert resp_assemble.status_code == 200, resp_assemble.text
    pkg_data = resp_assemble.json()
    pkg_id = pkg_data["media_package_id"]
    pl_id = pkg_data["playlist_session_id"]
    assert pkg_id.startswith("mpkg_")
    assert pl_id.startswith("ply_")
    assert pkg_data["status"] == "ready_for_publish"
    assert len(pkg_data["chapters"]) == 2

    # 2. Get package by ID
    resp_get = client.get(f"/media/packages/{pkg_id}", headers=headers)
    assert resp_get.status_code == 200
    assert resp_get.json()["media_package_id"] == pkg_id

    # 3. Get Seam C6 contract payload
    resp_c6 = client.get(f"/media/packages/{pkg_id}/c6", headers=headers)
    assert resp_c6.status_code == 200
    c6_data = resp_c6.json()
    assert c6_data["media_package_id"] == pkg_id
    assert "video" in c6_data
    assert "audio" in c6_data
    assert "chapters" in c6_data

    # 4. Get playlist session by ID
    resp_pl = client.get(f"/media/playlists/{pl_id}", headers=headers)
    assert resp_pl.status_code == 200
    assert resp_pl.json()["id"] == pl_id
    assert len(resp_pl.json()["tracks"]) == 2


def test_media_api_refuses_an_incomplete_track_manifest() -> None:
    token = "test-token"
    platform = load_settings(env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": token})
    app = create_app(
        session_factory=lambda: media_session(create_media_engine(static_pool=True)),
        settings=MediaFactorySettings(platform=platform),
        clock=FixedClock(NOW),
    )
    manifest = release_manifest().to_wire()
    del manifest["disposition"]
    resp = TestClient(app).post(
        "/media/packages/assemble",
        json={
            "track_manifests": [manifest],
            "brand_id": "brand_neon",
            "brand_name": "Neon Records",
            "required_duration_seconds": 150.0,
            "visual_profile": "brand_neon_rain_v1",
            "playlist_concept": "Deep Focus",
        },
        headers={SERVICE_TOKEN_HEADER: token},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "missing_required_field"
    assert "track_manifests[0].disposition" in resp.json()["detail"]["field_paths"]

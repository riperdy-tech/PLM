"""Unit tests for Music Factory API endpoints (§4.5, §67, §68, R6, R15)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from services.music_factory.api.app import create_app
from tests.support.music_db import create_music_engine, music_session

SAMPLE_C3_SPEC = {
    "track_spec_id": "trk_api_01",
    "playlist_spec_id": "ply_01",
    "experiment_id": "exp_api_01",
    "content_arm_id": "arm_01",
    "brand_profile_id": "neon_seoul",
    "hard_constraints": {"genre": "rnb", "language": "english", "vocal_mode": "vocal"},
    "bounded_preferences": {"bpm": 80, "mood": ["melancholic"], "use_case": ["late_night_drive"]},
    "creative_latitude": {},
    "lyric_spec": {},
    "vocal_spec": {"vocal_mode": "vocal"},
    "instrumentation_spec": {},
    "production_style": "lofi_contemporary",
    "track_role": "standout_single",
    "requested_duration": 90,
    "candidate_target": 1,
    "priority": 1,
    "budget_limit": 50.0,
    "provider_policy": {},
}


from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from services.music_factory.config import load_music_factory_settings

ENV_WITH_TOKEN = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "music-seam-token",
}


def _make_client(*, with_token: bool = True) -> tuple[TestClient, str]:
    env = dict(ENV_WITH_TOKEN)
    if not with_token:
        env.pop("PLM_SERVICE_TOKEN")
    platform = load_settings(env=env)
    settings = load_music_factory_settings(platform=platform)
    engine = create_music_engine(static_pool=True)

    def session_factory():
        return music_session(engine)

    app = create_app(session_factory=session_factory, settings=settings)
    client = TestClient(app)
    return client, "music-seam-token"


def test_health_check_public() -> None:
    client, _ = _make_client(with_token=False)
    res = client.get("/music/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_auth_fail_closed_without_token() -> None:
    client, token = _make_client(with_token=True)

    # Missing header -> 401
    res = client.get("/music/providers")
    assert res.status_code == 401

    # Invalid token -> 401
    res = client.get("/music/providers", headers={SERVICE_TOKEN_HEADER: "wrong-token"})
    assert res.status_code == 401

    # Valid token -> 200
    res = client.get("/music/providers", headers={SERVICE_TOKEN_HEADER: token})
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_plan_and_execution_lifecycle_and_r6_endpoints() -> None:
    client, token = _make_client(with_token=True)
    headers = {SERVICE_TOKEN_HEADER: token}

    # 1. Create plan
    res_plan = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=headers)
    assert res_plan.status_code == 200
    plan_data = res_plan.json()
    plan_id = plan_data["plan_id"]
    assert plan_data["status"] == "planned"

    # 2. Execute plan
    res_exec = client.post(f"/music/plans/{plan_id}/execute", json=SAMPLE_C3_SPEC, headers=headers)
    assert res_exec.status_code == 200
    exec_data = res_exec.json()
    assert exec_data["status"] == "completed"
    assert len(exec_data["candidates"]) >= 1

    candidate_id = exec_data["candidates"][0]["candidate_id"]

    # 3. Ruling R6 read endpoints:
    # GET /music/candidates/{id}/attempts
    res_att = client.get(f"/music/candidates/{candidate_id}/attempts", headers=headers)
    assert res_att.status_code == 200
    assert len(res_att.json()) >= 1

    # GET /music/candidates/{id}/license
    res_lic = client.get(f"/music/candidates/{candidate_id}/license", headers=headers)
    assert res_lic.status_code == 200
    lic_data = res_lic.json()
    assert lic_data["commercial_use_status"] == "permitted"
    assert lic_data["plan_or_tier"] == "pro_tier"
    assert lic_data["account_identity_ref"] == "plm-service-account"
    assert lic_data["terms_version"] == "2026-v1"

    # GET /music/candidates/{id}/artifacts
    res_art = client.get(f"/music/candidates/{candidate_id}/artifacts", headers=headers)
    assert res_art.status_code == 200
    assert len(res_art.json()) >= 1

    # GET /music/candidates/{id}/provenance
    res_prov = client.get(f"/music/candidates/{candidate_id}/provenance", headers=headers)
    assert res_prov.status_code == 200
    prov_data = res_prov.json()
    assert prov_data["candidate_id"] == candidate_id
    assert prov_data["track_spec_id"] == "trk_api_01"

    # 4. Export to P5 emitting Seam C4 event
    res_export = client.post(f"/music/candidates/{candidate_id}/export-to-p5", headers=headers)
    assert res_export.status_code == 200
    export_data = res_export.json()
    assert export_data["candidate"]["status"] == "exported_to_p5"
    assert export_data["event"]["event_type"] == "music_candidate_ready_for_p5"
    assert export_data["event"]["event_version"] == "1"
    assert export_data["event"]["measured_audio"]["duration_seconds"] > 0

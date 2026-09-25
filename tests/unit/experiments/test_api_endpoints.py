"""Tests for /experiments/v1 HTTP endpoints and auth (Task P3.10)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from plm_platform.config import load_settings
from sqlalchemy.orm import sessionmaker

from services.experiments.api.app import create_app
from services.experiments.config import load_experiments_settings
from tests.support.experiments_db import create_experiments_engine

ENV_WITH_TOKEN = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "experiments-seam-token",
}

SAMPLE_DIRECTIVE = {
    "directive_id": "dir_api_sample_01",
    "decision_id": "dec_api_01",
    "arm_id": "rnb_late_night_drive_english_vocal_45_75m",
    "allocation_type": "exploit",
    "priority": 1,
    "quantity": 1,
    "brief": {
        "genre": "rnb",
        "mood": ["melancholic", "intimate"],
        "use_case": ["late_night_drive"],
        "language": "english",
        "vocal_mode": "vocal",
        "duration_bucket": "45_75m",
        "visual_world": "rainy_asian_city",
    },
    "expected_value": 85.0,
    "uncertainty": 0.15,
    "budget_limit": 100.0,
    "market_context": {"brand_id": "neon_seoul"},
    "selection_reason": ["high_demand"],
    "directive_version": "p2-dir-v1",
    "policy_version": "p2-v1",
    "expires_at": "2026-10-01T00:00:00Z",
    "exploration_fraction": 0.2,
    "value_model": "early-stage-proxy-v1",
}


def _app(*, with_token: bool = True, with_db: bool = True) -> Any:
    env = dict(ENV_WITH_TOKEN)
    if not with_token:
        env.pop("PLM_SERVICE_TOKEN")
    settings = load_experiments_settings(platform=load_settings(env=env))

    if with_db:
        engine = create_experiments_engine(static_pool=True)
        factory = sessionmaker(bind=engine)
    else:
        factory = None

    return create_app(session_factory=factory, settings=settings)


def test_auth_rejection_and_fail_closed() -> None:
    # 1. Blocked when token is unconfigured (503)
    unconfigured = TestClient(_app(with_token=False))
    resp = unconfigured.get("/experiments/v1/experiments")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "blocked_pending_credentials"

    # 2. Refused when token is wrong (401)
    configured = TestClient(_app(with_token=True))
    wrong = configured.get(
        "/experiments/v1/experiments",
        headers={"X-PLM-Service-Token": "invalid-secret"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_service_token"
    assert "experiments-seam-token" not in wrong.text


def test_missing_database_returns_503() -> None:
    client = TestClient(_app(with_token=True, with_db=False))
    resp = client.get(
        "/experiments/v1/experiments",
        headers={"X-PLM-Service-Token": "experiments-seam-token"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "no_database"


def test_experiment_full_api_lifecycle() -> None:
    client = TestClient(_app(with_token=True, with_db=True))
    headers = {"X-PLM-Service-Token": "experiments-seam-token"}

    # 1. Create experiment
    payload = {
        "directive": SAMPLE_DIRECTIVE,
        "experiment_type": "single_factor",
        "seed": 42,
    }
    create_resp = client.post(
        "/experiments/v1/experiments",
        json=payload,
        headers=headers,
    )
    assert create_resp.status_code == 200, create_resp.text
    data = create_resp.json()
    exp = data["experiment"]
    exp_id = exp["experiment_id"]
    assert exp_id.startswith("exp_")
    assert exp["status"] == "validated"
    assert len(exp["treatments"]) == 2

    # 2. List experiments
    list_resp = client.get("/experiments/v1/experiments", headers=headers)
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["total"] >= 1
    assert any(e["experiment_id"] == exp_id for e in list_data["experiments"])

    # 3. Get experiment by ID
    get_resp = client.get(f"/experiments/v1/experiments/{exp_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["experiment_id"] == exp_id

    # 4. Get treatments
    trt_resp = client.get(f"/experiments/v1/experiments/{exp_id}/treatments", headers=headers)
    assert trt_resp.status_code == 200
    treatments = trt_resp.json()
    assert len(treatments) == 2

    # 5. Validate experiment
    val_resp = client.post(f"/experiments/v1/experiments/{exp_id}/validate", headers=headers)
    assert val_resp.status_code == 200
    report = val_resp.json()["report"]
    assert report["passed"] is True
    assert len(report["errors"]) == 0

    # 6. Materialize package
    target_trt_id = treatments[1]["treatment_id"]
    mat_payload = {
        "treatment_id": target_trt_id,
        "brand_id": "neon_seoul",
        "budget_envelope": 80.0,
        "track_count": 4,
        "total_duration_minutes": 50,
    }
    mat_resp = client.post(
        f"/experiments/v1/experiments/{exp_id}/materialize",
        json=mat_payload,
        headers=headers,
    )
    assert mat_resp.status_code == 200, mat_resp.text
    pkg = mat_resp.json()["package"]
    pkg_id = pkg["package_id"]
    assert pkg_id.startswith("pkg_")
    assert pkg["treatment_id"] == target_trt_id
    assert len(pkg["track_specs"]) == 4

    # 7. Get content package
    pkg_resp = client.get(f"/experiments/v1/packages/{pkg_id}", headers=headers)
    assert pkg_resp.status_code == 200
    assert pkg_resp.json()["package_id"] == pkg_id

    # Test alias path /content-packages/{id}
    alias_resp = client.get(f"/content-packages/{pkg_id}", headers=headers)
    assert alias_resp.status_code == 200
    assert alias_resp.json()["package_id"] == pkg_id

    # 8. Close experiment
    close_resp = client.post(f"/experiments/v1/experiments/{exp_id}/close", headers=headers)
    assert close_resp.status_code == 200
    assert close_resp.json()["status"] == "completed"

    # Verify status changed
    updated_exp = client.get(f"/experiments/v1/experiments/{exp_id}", headers=headers).json()
    assert updated_exp["status"] == "completed"


def test_api_404_handling() -> None:
    client = TestClient(_app(with_token=True, with_db=True))
    headers = {"X-PLM-Service-Token": "experiments-seam-token"}

    resp = client.get("/experiments/v1/experiments/exp_nonexistent", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "experiment_not_found"

    pkg_resp = client.get("/experiments/v1/packages/pkg_nonexistent", headers=headers)
    assert pkg_resp.status_code == 404
    assert pkg_resp.json()["detail"]["code"] == "package_not_found"

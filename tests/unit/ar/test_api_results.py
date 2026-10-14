# tests/unit/ar/test_api_results.py
"""P2.13: §72/§73 result ingestion and §74 updates over the API, once and once only."""

from fastapi.testclient import TestClient
from plm_platform.config import load_settings
from sqlalchemy.orm import sessionmaker

from services.autonomous_ar.api.app import create_app
from services.autonomous_ar.config import load_ar_settings
from tests.support.ar_db import create_ar_engine
from tests.support.seams import unmeasured_raw_metrics

ENV_WITH_TOKEN = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "ar-seam-token",
}

CYCLE_BODY = {
    "brand_id": "neon_seoul", "slot_count": 2, "budget": 2.0,
    "as_of": "2026-09-20T06:00:00+00:00", "policy_version": "p2-v1",
}

RESULT_BODY = {
    "schema_version": "1.0", "experiment_id": "exp-1", "arm_id": "rnb-0", "content_id": "c-1",
    "observation_window": "7d", "status": "mature",
    "observed_at": "2026-10-01T00:00:00+00:00", "raw_metrics": unmeasured_raw_metrics(),
    "normalized_reward": 0.8, "reward_version": "p9-reward-v1",
    "attribution_confidence": 0.9,
}


def test_results_are_accepted_once_and_updates_apply(client: TestClient) -> None:
    # drive one cycle so the arm state exists, then post a result for its first arm
    created = client.post("/ar/cycles", json=CYCLE_BODY)
    assert created.status_code == 200, created.text
    first = client.post("/ar/results", json=RESULT_BODY)
    assert first.status_code == 200, first.text
    assert first.json()["stored"] is True
    assert client.post("/ar/results", json=RESULT_BODY).json()["stored"] is False
    update = client.post("/ar/update", params={"brand_id": "neon_seoul"})
    assert update.status_code == 200, update.text
    assert update.json()["applied"] == 1
    state = client.get("/ar/arms/rnb-0/state").json()
    assert state["observation_count"] == 1
    assert client.post("/ar/results", json={**RESULT_BODY, "arm_id": "ghost"}).status_code == 422


def test_update_without_an_active_policy_is_409_never_a_silent_default() -> None:
    engine = create_ar_engine(static_pool=True)
    factory = sessionmaker(bind=engine)
    settings = load_ar_settings(platform=load_settings(env=ENV_WITH_TOKEN))
    app = create_app(session_factory=factory, settings=settings)
    with TestClient(app, headers={"X-PLM-Service-Token": "ar-seam-token"}) as client:
        response = client.post("/ar/update", params={"brand_id": "neon_seoul"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "policy_not_configured"

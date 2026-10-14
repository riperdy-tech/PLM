"""Unit tests for FastAPI `/analytics` endpoints (Phase P9)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from analytics.api.app import create_app
from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from tests.support.analytics_db import create_analytics_engine
from tests.support.seams import lineage_fields

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "test-analytics-token",
}


@pytest.fixture
def client() -> TestClient:
    engine = create_analytics_engine(static_pool=True)

    def session_factory():
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=engine)()

    settings = load_settings(env=MINIMAL_ENV)
    app = create_app(session_factory=session_factory, settings=settings, clock=FixedClock(NOW))
    return TestClient(app)


def test_health_endpoint_is_unauthenticated(client: TestClient) -> None:
    res = client.get("/analytics/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"
    assert res.json()["service"] == "analytics"
    assert res.json()["timestamp"] == NOW.isoformat()


def test_ingest_rejects_unauthenticated(client: TestClient) -> None:
    res = client.post("/analytics/ingest/youtube", json={})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "invalid_service_token"


def test_ingest_without_observation_time_is_refused(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-analytics-token"}
    yt_payload = {
        "video_id": "vid_api_02",
        "channel_id": "chan_api_01",
        "date_str": "2026-09-26",
        "views": 10,
        "watch_time_minutes": 1.0,
    }
    res = client.post("/analytics/ingest/youtube", json=yt_payload, headers=headers)
    assert res.status_code == 422
    assert any(e["loc"][-1] == "observed_at" for e in res.json()["detail"])

    cost_payload = {
        "entity_id": "vid_api_02",
        "entity_type": "video",
        "category": "compute",
        "amount_usd": 1.0,
        "period": "2026-09",
    }
    res = client.post("/analytics/ingest/cost", json=cost_payload, headers=headers)
    assert res.status_code == 422
    assert any(e["loc"][-1] == "incurred_at" for e in res.json()["detail"])


def test_analytics_api_lifecycle_roundtrip(client: TestClient) -> None:
    headers = {SERVICE_TOKEN_HEADER: "test-analytics-token"}

    # 1. Ingest YouTube metrics
    yt_payload = {
        "video_id": "vid_api_01",
        "channel_id": "chan_api_01",
        "date_str": "2026-09-26",
        "views": 10000,
        "watch_time_minutes": 320.0,
        "estimated_revenue_usd": 35.0,
        "window": "24h",
        "observed_at": "2026-10-01T11:00:00+00:00",
    }
    res_yt = client.post("/analytics/ingest/youtube", json=yt_payload, headers=headers)
    assert res_yt.status_code == 201, res_yt.text
    assert res_yt.json()["video_id"] == "vid_api_01"

    # 2. Ingest Cost
    cost_payload = {
        "entity_id": "vid_api_01",
        "entity_type": "video",
        "category": "compute",
        "amount_usd": 10.0,
        "period": "2026-09",
        "incurred_at": "2026-09-30T00:00:00+00:00",
    }
    res_cost = client.post("/analytics/ingest/cost", json=cost_payload, headers=headers)
    assert res_cost.status_code == 201, res_cost.text

    # 3. Get Economics
    res_econ = client.get("/analytics/economics/vid_api_01", headers=headers)
    assert res_econ.status_code == 200
    econ_data = res_econ.json()
    assert econ_data["gross_revenue_usd"] == 35.0
    assert econ_data["allocated_cost_usd"] == 10.0
    assert econ_data["net_contribution_usd"] == 25.0
    assert datetime.fromisoformat(econ_data["as_of"]) == NOW

    # 4. Post feedback
    fb_payload = {
        "lineage": lineage_fields(
            experiment_id="exp_api_01", experiment_family_id="fam_api_01",
            content_arm_id="arm_api_01", content_genome_id="gen_api_01",
        ),
        "unit_id": "vid_api_01",
        "net_contribution_usd": 25.0,
        "views": 10000,
        "watch_time_minutes": 320.0,
        "average_percentage_viewed": None,
        "subscribers_gained": None,
        "estimated_revenue": 30.0,
        "window": "7d",
        "maturity": "mature",
        "attribution_confidence": 1.0,
        "quality_status": None,
        "observed_at": "2026-10-01T12:00:00+00:00",
    }
    res_fb = client.post("/analytics/evaluation/feedback", json=fb_payload, headers=headers)
    assert res_fb.status_code == 200, res_fb.text
    fb_data = res_fb.json()
    assert fb_data["experiment_id"] == "exp_api_01"
    assert fb_data["schema_version"] == "1.0"
    assert fb_data["raw_metrics"]["subscriber_gain"] is None  # unmeasured stays null
    assert fb_data["normalized_reward"] > 0.0

    # 5. Get P10 features
    res_p10 = client.get("/analytics/p10/features?experiment_id=exp_api_01", headers=headers)
    assert res_p10.status_code == 200
    p10_data = res_p10.json()
    assert len(p10_data) == 1
    assert p10_data[0]["arm_id"] == "arm_api_01"

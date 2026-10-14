"""Unit tests for FastAPI `/analytics` endpoints (Phase P9, ADR-0031).

In-process TestClient over in-memory SQLite: these prove the routes and their refusals,
not the deployed service.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from analytics.api.app import create_app
from analytics.config import AnalyticsSettings
from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from tests.support.analytics_db import create_analytics_engine
from tests.support.seams import lineage_fields

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
HEADERS = {SERVICE_TOKEN_HEADER: "test-analytics-token"}

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


def _client(reporting_currency: str | None = None) -> TestClient:
    engine = create_analytics_engine(static_pool=True)

    def session_factory():
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=engine)()

    settings = load_settings(env=MINIMAL_ENV)
    p9 = None if reporting_currency is None else AnalyticsSettings(reporting_currency=reporting_currency)
    app = create_app(
        session_factory=session_factory, settings=settings, clock=FixedClock(NOW),
        analytics_settings=p9,
    )
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _client()  # default reporting currency: KRW


def _snapshot(window: str, *, views: int, revenue: str, observed_at: str) -> dict[str, Any]:
    return {
        "video_id": "vid_api_01", "channel_id": "chan_api_01", "date_str": observed_at[:10],
        "window": window, "views": views, "watch_time_minutes": 320.0,
        "estimated_revenue": revenue, "revenue_currency": "USD", "observed_at": observed_at,
    }


def _fx(client: TestClient) -> None:
    res = client.post(
        "/analytics/fx-rates",
        json={"rates": [{"base": "USD", "quote": "KRW", "rate": "1400", "source": "test_bank",
                         "as_of": "2026-08-01T00:00:00+00:00"}]},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text


def test_health_endpoint_is_unauthenticated(client: TestClient) -> None:
    res = client.get("/analytics/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"
    assert res.json()["service"] == "analytics"
    assert res.json()["timestamp"] == NOW.isoformat()


@pytest.mark.parametrize(
    "path", ["/analytics/ingest/youtube", "/analytics/fx-rates", "/analytics/evaluation/feedback"]
)
def test_writes_reject_unauthenticated(client: TestClient, path: str) -> None:
    res = client.post(path, json={})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "invalid_service_token"


def test_ingest_without_required_facts_is_refused(client: TestClient) -> None:
    yt_payload = {
        "video_id": "vid_api_02", "channel_id": "chan_api_01", "date_str": "2026-09-26",
        "views": 10, "watch_time_minutes": 1.0,
    }
    res = client.post("/analytics/ingest/youtube", json=yt_payload, headers=HEADERS)
    assert res.status_code == 422
    missing = {e["loc"][-1] for e in res.json()["detail"]}
    assert {"observed_at", "window"} <= missing

    cost_payload = {
        "entity_id": "vid_api_02", "entity_type": "video", "category": "compute",
        "amount": "1.00", "period": "2026-09",
    }
    res = client.post("/analytics/ingest/cost", json=cost_payload, headers=HEADERS)
    assert res.status_code == 422
    missing = {e["loc"][-1] for e in res.json()["detail"]}
    assert {"incurred_at", "currency"} <= missing


def test_youtube_revenue_without_currency_or_as_float_is_refused(client: TestClient) -> None:
    base = _snapshot("24h", views=10, revenue="1.00", observed_at="2026-09-30T00:00:00+00:00")
    no_currency = {k: v for k, v in base.items() if k != "revenue_currency"}
    res = client.post("/analytics/ingest/youtube", json=no_currency, headers=HEADERS)
    assert res.status_code == 422
    res = client.post("/analytics/ingest/youtube", json={**base, "estimated_revenue": 1.5},
                      headers=HEADERS)
    assert res.status_code == 422
    res = client.post("/analytics/ingest/youtube", json={**base, "estimated_revenue_usd": "1"},
                      headers=HEADERS)
    assert res.status_code == 422  # the old implicit-USD field is not silently ignored


def test_unmeasured_youtube_metrics_come_back_null(client: TestClient) -> None:
    payload = {"video_id": "vid_n", "channel_id": "c", "date_str": "2026-09-30", "window": "7d",
               "observed_at": "2026-09-30T00:00:00+00:00", "views": 5}
    res = client.post("/analytics/ingest/youtube", json=payload, headers=HEADERS)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["views"] == 5
    assert body["watch_time_minutes"] is None
    assert body["estimated_revenue"] is None


def test_statement_needs_a_currency_and_bad_lines_are_422(client: TestClient) -> None:
    stmt = {"provider": "spotify", "period": "2026-09", "source_reference": "s.csv",
            "raw_content": "isrc,track_id,streams,revenue\nUSPLM2600001,trk_api,10,\n"}
    res = client.post("/analytics/ingest/distributor", json=stmt, headers=HEADERS)
    assert res.status_code == 422
    assert any(e["loc"][-1] == "currency" for e in res.json()["detail"])
    res = client.post("/analytics/ingest/distributor", json={**stmt, "currency": "USD"},
                      headers=HEADERS)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "missing_required_field"


def test_gate_r2_api_economics_counts_overlapping_snapshots_once(client: TestClient) -> None:
    """24h + 7d + lifetime snapshots via the API: the economics endpoint counts revenue once."""
    _fx(client)
    for window, views, revenue, at in (
        ("24h", 1000, "2.00", "2026-09-02T00:00:00+00:00"),
        ("7d", 5000, "9.00", "2026-09-08T00:00:00+00:00"),
        ("lifetime", 12000, "20.00", "2026-09-30T00:00:00+00:00"),
    ):
        res = client.post("/analytics/ingest/youtube",
                          json=_snapshot(window, views=views, revenue=revenue, observed_at=at),
                          headers=HEADERS)
        assert res.status_code == 201, res.text

    res = client.get("/analytics/economics/vid_api_01", headers=HEADERS)
    assert res.status_code == 200, res.text
    econ = res.json()
    assert econ["window"] == "lifetime"
    assert econ["estimated_revenue"] == {"amount": "28000.000000", "currency": "KRW"}  # 20 USD
    assert econ["views"] == 12000
    assert econ["estimated_revenue_status"] == "estimated"
    assert econ["realized_revenue"] is None
    assert econ["net_contribution"] is None  # an estimate is never booked

    res = client.get("/analytics/economics/vid_api_01?window=7d", headers=HEADERS)
    assert res.json()["estimated_revenue"]["amount"] == "12600.000000"  # 9 USD


def test_economics_without_an_fx_rate_is_409(client: TestClient) -> None:
    res = client.post("/analytics/ingest/youtube",
                      json=_snapshot("lifetime", views=1, revenue="1.00",
                                     observed_at="2026-09-30T00:00:00+00:00"),
                      headers=HEADERS)
    assert res.status_code == 201
    res = client.get("/analytics/economics/vid_api_01", headers=HEADERS)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "fx_rate_missing"


def test_fx_rate_conflict_is_409(client: TestClient) -> None:
    _fx(client)
    _fx(client)  # replay: no-op
    res = client.post(
        "/analytics/fx-rates",
        json={"rates": [{"base": "USD", "quote": "KRW", "rate": "1500", "source": "test_bank",
                         "as_of": "2026-08-01T00:00:00+00:00"}]},
        headers=HEADERS,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "fx_rate_conflict"


def test_track_economics_nets_realized_revenue_against_cost(client: TestClient) -> None:
    _fx(client)
    res = client.post(
        "/analytics/ingest/distributor",
        json={"provider": "spotify", "period": "2026-09", "currency": "USD",
              "source_reference": "s.csv",
              "raw_content": "isrc,track_id,streams,revenue\nUSPLM2600001,trk_api,10000,40.00\n"},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text
    res = client.post(
        "/analytics/ingest/cost",
        json={"entity_id": "trk_api", "entity_type": "track", "category": "compute",
              "amount": "14000", "currency": "KRW", "period": "2026-09",
              "incurred_at": "2026-09-30T00:00:00+00:00"},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text
    res = client.get("/analytics/economics/trk_api?entity_type=track", headers=HEADERS)
    assert res.status_code == 200, res.text
    econ = res.json()
    assert econ["realized_revenue"] == {"amount": "56000.000000", "currency": "KRW"}
    assert econ["allocated_cost"] == {"amount": "14000.000000", "currency": "KRW"}
    assert econ["net_contribution"] == {"amount": "42000.000000", "currency": "KRW"}
    assert len(econ["fx_rate_ids"]) == 1
    assert datetime.fromisoformat(econ["as_of"]) == NOW


def _feedback(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "lineage": lineage_fields(
            experiment_id="exp_api_01", experiment_family_id="fam_api_01",
            content_arm_id="arm_api_01", content_genome_id="gen_api_01",
        ),
        "unit_id": "vid_api_01",
        "net_contribution": {"amount": "25.00", "currency": "USD"},
        "views": 10000,
        "watch_time_minutes": 320.0,
        "average_percentage_viewed": None,
        "subscribers_gained": None,
        "estimated_revenue": {"amount": "30.00", "currency": "USD"},
        "window": "7d",
        "published_at": "2026-09-20T12:00:00+00:00",
        "attribution_confidence": 1.0,
        "quality_status": None,
        "observed_at": "2026-10-01T12:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_feedback_derives_maturity_and_converts_to_the_reporting_currency(
    client: TestClient,
) -> None:
    _fx(client)
    res = client.post("/analytics/evaluation/feedback", json=_feedback(), headers=HEADERS)
    assert res.status_code == 200, res.text
    fb = res.json()
    assert fb["experiment_id"] == "exp_api_01"
    assert fb["status"] == "mature"  # derived: published 11 days before a 7d observation
    assert fb["raw_metrics"]["subscriber_gain"] is None  # unmeasured stays null
    assert fb["raw_metrics"]["estimated_revenue"] == 42000.0  # 30 USD in KRW
    assert fb["reward_version"] == "engagement-contribution-proxy-v1"
    assert 0.0 < fb["normalized_reward"] < 1.0

    res = client.get("/analytics/p10/features?experiment_id=exp_api_01", headers=HEADERS)
    assert res.status_code == 200
    p10 = res.json()
    assert len(p10) == 1
    assert p10[0]["arm_id"] == "arm_api_01"
    assert p10[0]["primary_outcome"]["value"] == 35000.0  # 25 USD in KRW


def test_feedback_provisional_when_the_window_has_not_elapsed(client: TestClient) -> None:
    _fx(client)
    res = client.post("/analytics/evaluation/feedback",
                      json=_feedback(published_at="2026-09-28T12:00:00+00:00"), headers=HEADERS)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "provisional"


def test_feedback_refuses_caller_asserted_maturity(client: TestClient) -> None:
    res = client.post("/analytics/evaluation/feedback", json=_feedback(maturity="mature"),
                      headers=HEADERS)
    assert res.status_code == 422


def test_feedback_refuses_an_observation_before_publication(client: TestClient) -> None:
    res = client.post("/analytics/evaluation/feedback",
                      json=_feedback(published_at="2026-10-02T00:00:00+00:00"), headers=HEADERS)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "observation_precedes_publication"


def test_feedback_without_an_fx_rate_is_409(client: TestClient) -> None:
    res = client.post("/analytics/evaluation/feedback", json=_feedback(), headers=HEADERS)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "fx_rate_missing"


def test_feedback_refuses_float_amounts_and_missing_engagement(client: TestClient) -> None:
    res = client.post("/analytics/evaluation/feedback",
                      json=_feedback(net_contribution={"amount": 25.0, "currency": "USD"}),
                      headers=HEADERS)
    assert res.status_code == 422
    res = client.post("/analytics/evaluation/feedback", json=_feedback(views=None),
                      headers=HEADERS)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "missing_required_field"


def test_reporting_currency_is_a_setting() -> None:
    usd_client = _client("USD")
    res = usd_client.post("/analytics/evaluation/feedback", json=_feedback(), headers=HEADERS)
    assert res.status_code == 200, res.text  # no FX needed: everything is already USD
    assert res.json()["raw_metrics"]["estimated_revenue"] == 30.0

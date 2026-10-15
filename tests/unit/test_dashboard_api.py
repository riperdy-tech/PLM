"""Unit tests for the demo dashboard (``plm dashboard --demo``).

The demo app serves fixed sample data, so the tests pin three properties beyond the
routes answering: every payload and response is labelled ``demo``, mutating routes fail
closed without the service token (R15), and cross-origin access is loopback-only. The
live dashboard is covered by ``test_dashboard_live.py`` and ``test_dashboard_exceptions.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.dashboard import DEMO_DATA_SOURCE as DATA_SOURCE
from plm_platform.dashboard import create_demo_app
from plm_platform.dashboard.app import DATA_SOURCE_HEADER

TOKEN = "dashboard-test-token"
PLATFORM_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": TOKEN,
}
AUTH = {SERVICE_TOKEN_HEADER: TOKEN}


def _client(*, with_token: bool = True) -> TestClient:
    platform = load_settings(env=PLATFORM_ENV, dotenv_path="does-not-exist.env")
    return TestClient(create_demo_app(platform=platform if with_token else None))


def test_data_source_is_demo() -> None:
    assert DATA_SOURCE == "demo"


@pytest.mark.parametrize("path", ["/api/arms", "/api/candidates", "/api/releases"])
def test_list_endpoints_are_labelled_envelopes(path: str) -> None:
    r = _client().get(path)
    assert r.status_code == 200
    body = r.json()
    assert body["data_source"] == DATA_SOURCE
    assert isinstance(body["items"], list)
    assert body["items"]
    assert r.headers[DATA_SOURCE_HEADER] == DATA_SOURCE


def test_stats_are_labelled() -> None:
    r = _client().get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["data_source"] == DATA_SOURCE
    assert "active_arms" in data
    assert "total_cycles" in data


def test_arms_carry_posteriors() -> None:
    arms = _client().get("/api/arms").json()["items"]
    assert "posterior_mean" in arms[0]


def test_audio_stream_is_labelled_by_header() -> None:
    r = _client().get("/api/audio/cand_synth_01")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.headers[DATA_SOURCE_HEADER] == DATA_SOURCE
    assert r.content.startswith(b"RIFF")


def test_pipeline_run_with_token_is_labelled() -> None:
    r = _client().post("/api/pipeline/run", json={"mode": "full"}, headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "started"
    assert data["data_source"] == DATA_SOURCE


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/pipeline/run", {"mode": "full"}),
        ("/api/qa/resolve", {"case_id": "case_001", "resolution": "CLEARED_BY_OVERRIDE"}),
    ],
)
def test_mutating_routes_refuse_a_missing_or_wrong_token(path: str, body: dict[str, str]) -> None:
    client = _client()
    assert client.post(path, json=body).status_code == 401
    wrong = client.post(path, json=body, headers={SERVICE_TOKEN_HEADER: "nope"})
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_service_token"


@pytest.mark.parametrize("path", ["/api/pipeline/run", "/api/qa/resolve"])
def test_mutating_routes_fail_closed_without_configured_token(path: str) -> None:
    r = _client(with_token=False).post(
        path, json={"case_id": "case_001"}, headers=AUTH
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "blocked_pending_credentials"


def test_qa_resolve_known_case() -> None:
    r = _client().post(
        "/api/qa/resolve",
        json={"case_id": "case_001", "resolution": "CLEARED_BY_OVERRIDE", "notes": "test"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["resolution"] == "CLEARED_BY_OVERRIDE"
    assert r.json()["data_source"] == DATA_SOURCE


def test_qa_resolve_unknown_case_is_404_not_resolved() -> None:
    r = _client().post(
        "/api/qa/resolve",
        json={"case_id": "case_does_not_exist", "resolution": "CLEARED_BY_OVERRIDE"},
        headers=AUTH,
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_case"


def test_cors_allows_loopback_without_credentials() -> None:
    r = _client().get("/api/stats", headers={"Origin": "http://127.0.0.1:8050"})
    assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:8050"
    assert "access-control-allow-credentials" not in r.headers


def test_cors_rejects_foreign_origin() -> None:
    r = _client().get("/api/stats", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_root_html_shows_demo_banner() -> None:
    client = _client()
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "CONTROL DECK" in r.text
    assert "DEMO DATA" in r.text
    assert "not connected to the pipeline" in r.text

    r_css = client.get("/static/app.css")
    assert r_css.status_code == 200
    assert "text/css" in r_css.headers["content-type"]
    assert ".demo-banner" in r_css.text

    r_js = client.get("/static/app.js")
    assert r_js.status_code == 200
    assert "javascript" in r_js.headers["content-type"]
    assert "initTabs" in r_js.text
    assert SERVICE_TOKEN_HEADER in r_js.text

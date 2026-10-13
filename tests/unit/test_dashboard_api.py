"""Unit tests for the dashboard FastAPI application and endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from packages.plm_platform.dashboard.app import create_app


def test_dashboard_api_stats() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert "active_arms" in data
    assert "total_cycles" in data


def test_dashboard_api_arms() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/arms")
    assert r.status_code == 200
    arms = r.json()
    assert isinstance(arms, list)
    assert len(arms) > 0
    assert "posterior_mean" in arms[0]


def test_dashboard_api_candidates() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/candidates")
    assert r.status_code == 200
    candidates = r.json()
    assert isinstance(candidates, list)
    assert len(candidates) > 0


def test_dashboard_api_audio_stream() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/audio/cand_synth_01")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert len(r.content) > 1000
    assert r.content.startswith(b"RIFF")


def test_dashboard_api_releases() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/releases")
    assert r.status_code == 200
    releases = r.json()
    assert isinstance(releases, list)
    assert len(releases) > 0


def test_dashboard_api_pipeline_run() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/pipeline/run", json={"mode": "full"})
    assert r.status_code == 200
    data = r.json()
    assert "run_id" in data
    assert data["status"] == "started"


def test_dashboard_api_qa_resolve() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.post(
        "/api/qa/resolve",
        json={
            "case_id": "case_001",
            "resolution": "CLEARED_BY_OVERRIDE",
            "notes": "Verified by QA test suite",
        },
    )
    assert r.status_code == 200
    assert r.json()["resolution"] == "CLEARED_BY_OVERRIDE"


def test_dashboard_root_html() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "CONTROL DECK" in r.text

    # Test static assets
    r_css = client.get("/static/app.css")
    assert r_css.status_code == 200
    assert "text/css" in r_css.headers["content-type"]
    assert "--accent-violet" in r_css.text

    r_js = client.get("/static/app.js")
    assert r_js.status_code == 200
    assert "javascript" in r_js.headers["content-type"]
    assert "initTabs" in r_js.text

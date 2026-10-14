"""The shared liveness/readiness surface every phase API mounts."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from plm_platform.health import health_router
from sqlalchemy.orm import Session, sessionmaker


def _client(session_factory: object) -> TestClient:
    app = FastAPI()
    app.include_router(health_router("market", session_factory))  # type: ignore[arg-type]
    return TestClient(app)


def test_alive_without_touching_dependencies() -> None:
    assert _client(None).get("/healthz").json() == {"service": "market", "status": "alive"}


def test_not_ready_without_a_database() -> None:
    response = _client(None).get("/readyz")
    assert response.status_code == 503
    assert response.json()["reason"] == "database_not_configured"


def test_ready_when_the_database_answers() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    response = _client(sessionmaker(bind=engine)).get("/readyz")
    assert response.status_code == 200 and response.json()["status"] == "ready"


def test_not_ready_when_the_database_is_unreachable() -> None:
    def broken() -> Session:
        engine = sa.create_engine("sqlite+pysqlite:////nonexistent-dir/x.db")
        return sessionmaker(bind=engine)()

    response = _client(broken).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"service": "market", "status": "not_ready",
                               "reason": "database_unreachable"}

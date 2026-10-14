"""One liveness and readiness surface for every phase API (R3 runtime).

* ``GET /healthz``: the process is up and serving. No dependencies are touched, so an
  orchestrator never restarts a healthy process because its database blipped.
* ``GET /readyz``: the service can do its job now. It runs one ``SELECT 1`` through the
  service's own session factory and answers 503 with the reason when it cannot. A service
  started without a database (``session_factory=None``) is alive but never ready.

Both are unauthenticated and reveal no configuration: the body names the service and the
failing dependency, never a URL or credential.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

__all__ = ["health_router"]


def health_router(service: str, session_factory: Callable[[], Session] | None) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"service": service, "status": "alive"}

    @router.get("/readyz")
    def readyz() -> JSONResponse:
        if session_factory is None:
            return JSONResponse(
                status_code=503,
                content={"service": service, "status": "not_ready",
                         "reason": "database_not_configured"},
            )
        try:
            with session_factory() as session:
                session.execute(sa.text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={"service": service, "status": "not_ready",
                         "reason": "database_unreachable"},
            )
        return JSONResponse(status_code=200, content={"service": service, "status": "ready"})

    return router

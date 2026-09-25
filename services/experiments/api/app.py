"""The `/experiments` service surface (PHASE-P3 §49, §71).

FastAPI, correlation middleware, fail-closed auth.
`session_factory=None` yields 503s instead of crashing at import.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from plm_contracts import ErrorShape
from plm_platform.correlation import CorrelationIdMiddleware
from sqlalchemy.orm import Session

from ..config import ExperimentsSettings
from .routes import build_router

__all__ = ["create_app"]


def create_app(
    *,
    session_factory: Callable[[], Session] | None,
    settings: ExperimentsSettings,
) -> FastAPI:
    """Create FastAPI application for Experiments service."""
    app = FastAPI(title="plm-experiments", version="p3")
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(
        build_router(
            session_factory=session_factory,
            settings=settings,
        )
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Fail closed with the shared error shape: a 500 is still machine-readable.
        return JSONResponse(
            status_code=500,
            content=ErrorShape(
                code="internal_error",
                field_paths=[],
                message=f"{type(exc).__name__}: {exc}",
            ).model_dump(),
        )

    return app

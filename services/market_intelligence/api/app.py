# services/market_intelligence/api/app.py
"""The P1 API application factory. Mounted at `/market/v1` (R15)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from plm_platform.health import health_router
from plm_platform.observability import CorrelationIdMiddleware
from sqlalchemy.orm import Session

from ..config import MarketSettings
from .routes import build_router

__all__ = ["API_PREFIX", "create_app"]

API_PREFIX = "/market/v1"


def create_app(*, session_factory: Callable[[], Session] | None,
               settings: MarketSettings) -> FastAPI:
    """Build the app; `session_factory=None` yields 503s instead of crashing at import."""
    app = FastAPI(title="PLM market intelligence", version=settings.score_version,
                  docs_url=None, redoc_url=None)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(
        build_router(session_factory=session_factory, settings=settings), prefix=API_PREFIX
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        from plm_contracts import ErrorShape

        return JSONResponse(status_code=500, content=ErrorShape(
            code="internal_error", field_paths=[],
            message=f"{type(exc).__name__}: {exc}",
        ).model_dump())

    app.include_router(health_router("market", session_factory))
    return app

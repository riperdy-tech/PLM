"""The `/ar` service surface (§71). FastAPI, correlation middleware, fail-closed auth.

`session_factory=None` yields 503s instead of crashing at import; `market_source` is the
P1 test seam (production passes `None` and the route builds the real `P1Client`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from plm_platform.correlation import CorrelationIdMiddleware
from sqlalchemy.orm import Session

from ..config import ARSettings
from .routes import build_router

__all__ = ["create_app"]


def create_app(*, session_factory: Callable[[], Session] | None, settings: ARSettings,
               market_source: Callable[[Any], Any] | None = None) -> FastAPI:
    app = FastAPI(title="plm-ar", version="p2")
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(build_router(session_factory=session_factory, settings=settings,
                                    market_source=market_source))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Fail closed with the shared error shape: a 500 is still machine-readable.
        from plm_contracts import ErrorShape

        return JSONResponse(status_code=500, content=ErrorShape(
            code="internal_error", field_paths=[],
            message=f"{type(exc).__name__}: {exc}",
        ).model_dump())

    return app

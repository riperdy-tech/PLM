"""The `/media` service surface (P6 §5, §8, Ruling R9, R15)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from plm_contracts import ErrorShape
from plm_platform.clock import Clock
from plm_platform.correlation import CorrelationIdMiddleware
from sqlalchemy.orm import Session

from ..config import MediaFactorySettings
from .routes import build_router

__all__ = ["create_app"]


def create_app(
    *,
    session_factory: Callable[[], Session] | None,
    settings: MediaFactorySettings,
    clock: Clock,
) -> FastAPI:
    """Create FastAPI application for Playlist, Visual & Video Factory service."""
    app = FastAPI(title="plm-media-factory", version="p6")
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(
        build_router(
            session_factory=session_factory,
            settings=settings,
            clock=clock,
        )
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=ErrorShape(
                code="internal_error",
                field_paths=[],
                message=f"{type(exc).__name__}: {exc}",
            ).model_dump(),
        )

    return app

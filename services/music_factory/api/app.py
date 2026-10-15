"""The `/music` service surface (§4.5, §67, §68, R6, R15)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from plm_contracts import ErrorShape
from plm_platform.correlation import CorrelationIdMiddleware
from plm_platform.health import health_router
from sqlalchemy.orm import Session

from ..config import MusicFactorySettings
from ..storage.artifacts import ArtifactStorageProtocol, MinioArtifactStorage
from .routes import build_router

__all__ = ["create_app"]


def create_app(
    *,
    session_factory: Callable[[], Session] | None,
    settings: MusicFactorySettings,
    storage: ArtifactStorageProtocol | None = None,
) -> FastAPI:
    """Create FastAPI application for Music Generation Factory service.

    The artifact store is ``storage`` when given (tests inject one), otherwise MinIO
    from the platform settings (R4). With neither, generation answers 503
    ``artifact_store_not_configured``; there is no in-memory fallback.
    """
    if storage is None and settings.platform is not None:
        storage = MinioArtifactStorage(settings.platform)
    app = FastAPI(title="plm-music-factory", version="p4")
    app.state.artifact_storage = storage  # which store this service writes to, for probes
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(
        build_router(
            session_factory=session_factory,
            settings=settings,
            storage=storage,
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

    app.include_router(health_router("music", session_factory))
    return app

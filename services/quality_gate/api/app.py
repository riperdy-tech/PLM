"""The `/qa` service surface (P5 §4.5, §94, R7, R8, R15)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from plm_contracts import ErrorShape
from plm_platform.correlation import CorrelationIdMiddleware
from plm_platform.health import health_router
from sqlalchemy.orm import Session

from ..config import QualityGateSettings
from ..ingestion.artifact_fetch import ArtifactFetcher, MinioArtifactFetcher
from .routes import build_router

__all__ = ["create_app"]


def create_app(
    *,
    session_factory: Callable[[], Session] | None,
    settings: QualityGateSettings,
    artifact_fetcher: ArtifactFetcher | None = None,
) -> FastAPI:
    """Create FastAPI application for Quality, Rights & Release Gate service.

    Audio is read by ``artifact_fetcher`` when given (tests inject one), otherwise from
    MinIO per the platform settings (R4). With neither, evaluations answer 503
    ``artifact_store_not_configured``.
    """
    if artifact_fetcher is None and settings.platform is not None:
        artifact_fetcher = MinioArtifactFetcher(settings.platform)
    app = FastAPI(title="plm-quality-gate", version="p5")
    app.state.artifact_fetcher = artifact_fetcher  # which store P5 reads from, for probes
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(
        build_router(
            session_factory=session_factory,
            settings=settings,
            artifact_fetcher=artifact_fetcher,
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

    app.include_router(health_router("qa", session_factory))
    return app

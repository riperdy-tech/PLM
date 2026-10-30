"""The `/music` service surface (§4.5, §67, §68, R6, R15)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from plm_contracts import ErrorShape
from plm_platform.clock import Clock, SystemClock
from plm_platform.correlation import CorrelationIdMiddleware
from plm_platform.execution import (
    ExecutionSettings,
    ExternalWriteGuard,
    load_execution_settings,
    load_write_authorizations,
)
from plm_platform.health import health_router
from plm_platform.ledger import budget_gate_for
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
    execution: ExecutionSettings | None = None,
    write_guard: ExternalWriteGuard | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Create FastAPI application for Music Generation Factory service.

    The artifact store is ``storage`` when given (tests inject one), otherwise MinIO
    from the platform settings (R4). With neither, generation answers 503
    ``artifact_store_not_configured``; there is no in-memory fallback.

    Paid generation goes through ``write_guard`` (R5). Unless one is injected, the guard
    uses the configured execution policies (``PLM_EXECUTION_POLICY__MUSIC_GENERATE``) and
    the persisted budget ledger when ``PLM_BUDGET_LEDGER_CURRENCY`` is set, else
    ``NoBudgetLedger`` (every live call refused), and the owner's time-limited grants from
    ``PLM_WRITE_AUTHORIZATIONS_FILE`` (none: every live call refused). A mock provider
    needs none of them.
    """
    service_clock = SystemClock() if clock is None else clock
    if write_guard is None:
        execution_settings = load_execution_settings() if execution is None else execution
        write_guard = ExternalWriteGuard(
            execution_settings,
            authorizations=load_write_authorizations(),
            budget=(budget_gate_for(execution_settings, session_factory, clock=service_clock)
                    if session_factory is not None else None),
        )
    if storage is None and settings.platform is not None:
        storage = MinioArtifactStorage(settings.platform)
    app = FastAPI(title="plm-music-factory", version="p4")
    app.state.artifact_storage = storage  # which store this service writes to, for probes
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(
        build_router(
            session_factory=session_factory,
            settings=settings,
            clock=service_clock,
            storage=storage,
            write_guard=write_guard,
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

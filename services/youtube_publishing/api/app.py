"""FastAPI application factory for Phase P7 Autonomous YouTube Publishing Engine."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from plm_contracts import ProviderMode
from plm_platform.clock import Clock, SystemClock
from plm_platform.config import Settings, load_settings
from plm_platform.execution import (
    ExecutionSettings,
    ExternalWriteGuard,
    IntegrationRequirement,
    load_execution_settings,
    provider_mode_of,
    validate_integrations,
)

from .routes import create_youtube_router
from .service_token import require_service_token


def create_app(
    *,
    session_factory: Any = None,
    settings: Settings | None = None,
    client: Any = None,
    execution: ExecutionSettings | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Create a configured FastAPI application instance for the `/youtube` service.

    Fails at boot when the configured YouTube provider mode has no implementation
    (only the mock exists today, unless a client is injected). ``clock`` is the one time
    source for every route, engine run and write-guard decision; the wall clock is used
    only when none is injected.
    """
    service_clock = SystemClock() if clock is None else clock
    platform_settings = load_settings() if settings is None else settings
    execution_settings = load_execution_settings() if execution is None else execution
    implemented = frozenset({ProviderMode.MOCK if client is None else provider_mode_of(client)})
    validate_integrations(
        [IntegrationRequirement("youtube", implemented=implemented, required=True)],
        execution_settings,
    )
    auth_dep = require_service_token(platform_settings)

    app = FastAPI(
        title="PLM YouTube Publishing Engine",
        version="0.1.0",
        docs_url="/youtube/docs",
        openapi_url="/youtube/openapi.json",
    )

    if session_factory is None:
        from plm_platform.db import build_engine
        from plm_platform.db import session_factory as default_factory

        engine = build_engine(platform_settings)
        session_factory = default_factory(engine)

    router = create_youtube_router(
        session_factory,
        auth_dep,
        client=client,
        clock=service_clock,
        write_guard=ExternalWriteGuard(execution_settings),
    )
    app.include_router(router)
    return app

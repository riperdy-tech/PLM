"""FastAPI application factory for Phase P8 DSP / Music Distribution Engine."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from plm_contracts import ProviderMode
from plm_platform.config import Settings, load_settings
from plm_platform.execution import (
    ExecutionSettings,
    ExternalWriteGuard,
    IntegrationRequirement,
    load_execution_settings,
    provider_mode_of,
    validate_integrations,
)

from ..engine import DistributionEngine
from .routes import build_distribution_router
from .service_token import require_service_token


def create_app(
    *,
    session_factory: Any = None,
    settings: Settings | None = None,
    engine: DistributionEngine | None = None,
    execution: ExecutionSettings | None = None,
) -> FastAPI:
    """Create a configured FastAPI application instance for the `/distribution` service.

    Fails at boot when the configured distributor provider mode has no implementation
    (only the mock exists today, unless an engine with a real client is injected).
    """
    platform_settings = load_settings() if settings is None else settings
    execution_settings = load_execution_settings() if execution is None else execution
    implemented = frozenset(
        {ProviderMode.MOCK if engine is None else provider_mode_of(engine.client)}
    )
    validate_integrations(
        [IntegrationRequirement("distribution", implemented=implemented, required=True)],
        execution_settings,
    )
    auth_dep = require_service_token(platform_settings)

    app = FastAPI(
        title="PLM DSP Distribution Engine",
        version="0.1.0",
        docs_url="/distribution/docs",
        openapi_url="/distribution/openapi.json",
    )

    if session_factory is None:
        from plm_platform.db import build_engine
        from plm_platform.db import session_factory as default_factory

        db_engine = build_engine(platform_settings)
        session_factory = default_factory(db_engine)

    router = build_distribution_router(
        session_factory,
        auth_dep,
        engine=engine,
        write_guard=ExternalWriteGuard(execution_settings),
    )
    app.include_router(router)
    return app

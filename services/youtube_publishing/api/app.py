"""FastAPI application factory for Phase P7 Autonomous YouTube Publishing Engine."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from plm_contracts import ProviderMode
from plm_platform.clearance import ClearanceVerifier, clearance_verifier_for
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
from plm_platform.health import health_router
from plm_platform.ledger import budget_gate_for
from plm_platform.testing.mock_outcomes import mock_outcome_model_from_env

from ..analytics import MockYouTubeAnalytics, YouTubeAnalyticsSource
from .routes import create_youtube_router
from .service_token import require_service_token


def create_app(
    *,
    session_factory: Any = None,
    settings: Settings | None = None,
    client: Any = None,
    execution: ExecutionSettings | None = None,
    clock: Clock | None = None,
    clearance: ClearanceVerifier | None = None,
    analytics: YouTubeAnalyticsSource | None = None,
    analytics_from_env: bool = True,
) -> FastAPI:
    """Create a configured FastAPI application instance for the `/youtube` service.

    Fails at boot when the configured YouTube provider mode has no implementation
    (only the mock exists today, unless a client is injected). ``clock`` is the one time
    source for every route, engine run and write-guard decision; the wall clock is used
    only when none is injected. The write guard's budget gate is the persisted ledger
    when ``PLM_BUDGET_LEDGER_CURRENCY`` is configured, else ``NoBudgetLedger`` (every live
    write refused).

    The YouTube Analytics source is ``analytics`` when injected. Otherwise, for a mock
    YouTube provider and ``analytics_from_env``, it is the mock source over the outcome
    model named by ``PLM_MOCK_OUTCOMES_FILE``; unset (or a real provider, before R6),
    there is none and the analytics route refuses.
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
    if analytics is None and analytics_from_env and ProviderMode.MOCK in implemented:
        model = mock_outcome_model_from_env()
        analytics = None if model is None else MockYouTubeAnalytics(model)

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
        clearance=(
            clearance_verifier_for(platform_settings, session_factory)
            if clearance is None else clearance
        ),
        write_guard=ExternalWriteGuard(
            execution_settings,
            budget=budget_gate_for(execution_settings, session_factory, clock=service_clock),
        ),
        analytics=analytics,
    )
    app.include_router(router)
    app.include_router(health_router("youtube", session_factory))
    return app

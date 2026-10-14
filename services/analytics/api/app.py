"""FastAPI application factory for Phase P9 Analytics & Economics Engine."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from plm_platform.clock import Clock, SystemClock
from plm_platform.config import Settings, load_settings

from .routes import build_analytics_router
from .service_token import require_service_token


def create_app(
    *,
    session_factory: Any = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Create a configured FastAPI application instance for the `/analytics` service."""
    platform_settings = load_settings() if settings is None else settings
    auth_dep = require_service_token(platform_settings)

    app = FastAPI(
        title="PLM Analytics & Economics Engine",
        version="0.1.0",
        docs_url="/analytics/docs",
        openapi_url="/analytics/openapi.json",
    )

    if session_factory is None:
        from plm_platform.db import build_engine
        from plm_platform.db import session_factory as default_factory

        db_engine = build_engine(platform_settings)
        session_factory = default_factory(db_engine)

    router = build_analytics_router(
        session_factory, auth_dep, clock=SystemClock() if clock is None else clock
    )
    app.include_router(router)
    return app

"""FastAPI application factory for Phase P7 Autonomous YouTube Publishing Engine."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from plm_platform.config import Settings, load_settings

from .routes import create_youtube_router
from .service_token import require_service_token


def create_app(
    *,
    session_factory: Any = None,
    settings: Settings | None = None,
    client: Any = None,
) -> FastAPI:
    """Create a configured FastAPI application instance for the `/youtube` service."""
    platform_settings = load_settings() if settings is None else settings
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

    router = create_youtube_router(session_factory, auth_dep, client=client)
    app.include_router(router)
    return app

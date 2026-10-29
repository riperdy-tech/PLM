"""FastAPI application factory for Phase P9 Analytics & Economics Engine."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI
from plm_platform.clock import Clock, SystemClock
from plm_platform.config import Settings, load_settings
from plm_platform.health import health_router
from plm_platform.service_urls import service_url
from plm_platform.temporal import Phase

from ..config import AnalyticsSettings, load_analytics_settings
from ..ingestion.youtube_client import RealYouTubeAnalyticsClient
from ..ingestion.youtube_reporting import AuthenticatedReachDownloader
from .routes import build_analytics_router
from .service_token import require_service_token


def create_app(
    *,
    session_factory: Any = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
    analytics_settings: AnalyticsSettings | None = None,
    reward_client: httpx.Client | None = None,
    youtube_client: httpx.Client | None = None,
    reporting_downloader: AuthenticatedReachDownloader | None = None,
) -> FastAPI:
    """Create a configured FastAPI application instance for the `/analytics` service.

    Reward delivery over ``POST /analytics/rewards/dispatch`` uses ``reward_client`` when
    injected, else a client for P2's base URL (``PLM_SERVICE_URL__AR``, or the compose
    service name) with the service token; without a token it refuses.
    """
    platform_settings = load_settings() if settings is None else settings
    p9_settings = (
        load_analytics_settings(platform=platform_settings)
        if analytics_settings is None
        else analytics_settings
    )
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

    token = (None if platform_settings.service_token is None
             else platform_settings.service_token.get_secret_value())
    if reward_client is None and token:
        reward_client = httpx.Client(base_url=service_url(Phase.AR),
                                     timeout=p9_settings.ar_timeout_seconds)
    if youtube_client is None and token:
        youtube_client = httpx.Client(base_url=service_url(Phase.YOUTUBE), timeout=10.0)
    if reporting_downloader is None:
        reporting_oauth = RealYouTubeAnalyticsClient()
        if reporting_oauth.is_configured:
            reporting_downloader = AuthenticatedReachDownloader(
                client=httpx.Client(timeout=30.0, follow_redirects=False),
                access_token=reporting_oauth._get_access_token,
            )
    router = build_analytics_router(
        session_factory,
        auth_dep,
        clock=SystemClock() if clock is None else clock,
        settings=p9_settings,
        reward_client=reward_client,
        reward_token=token,
        youtube_client=youtube_client,
        youtube_token=token,
        reporting_downloader=reporting_downloader,
    )
    app.include_router(router)
    app.include_router(health_router("analytics", session_factory))
    return app

"""FastAPI applications for the PLM dashboard: live by default, demo only behind ``--demo``.

**Live** (:func:`create_live_app`): the six architecture PART XVII views over the
database (read-only) and the phase APIs (:mod:`.live`), plus the Exceptions actions that
drive the R3 exception queue (:mod:`.actions`). Every payload carries
``data_source: "live"`` and every response the ``X-PLM-Data-Source: live`` header. A
source that cannot answer makes its panel ``unavailable``; live mode never serves demo
data and has no demo route.

**Demo** (:func:`create_demo_app`): the fixed sample data and scripted playback of
:mod:`.service` and :mod:`.orchestrator`, every payload labelled ``demo`` and the page
under a DEMO DATA banner. It reads and drives nothing.

Mutating routes require the platform service token and fail closed when none is
configured (R15). Exception actions additionally need a reviewer directory
(``PLM_DASHBOARD_REVIEWERS_FILE``); without one they are refused. Cross-origin access is
limited to loopback origins without credentials.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any, Final

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from plm_contracts import ErrorShape
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from ..auth import require_service_token
from ..clock import Clock
from ..config import SERVICE_TOKEN_HEADER, Settings
from .actions import (
    ActionRefused,
    ExceptionActions,
    PipelineSignaller,
    ReviewerConfig,
    TemporalSignaller,
)
from .live import VIEW_NAMES, LiveDashboard
from .orchestrator import DemoOrchestrator
from .service import DEMO_DATA_SOURCE, DashboardService, UnknownCaseError
from .sources import LIVE_DATA_SOURCE, PhaseApis, ReadOnlyDatabase, SourceUnavailable

__all__ = [
    "DATA_SOURCE_HEADER",
    "build_live_components",
    "create_app",
    "create_demo_app",
    "create_live_app",
]

STATIC_DIR = Path(__file__).resolve().parent / "static"
LIVE_STATIC_DIR = STATIC_DIR / "live"
DEMO_STATIC_DIR = STATIC_DIR / "demo"

DATA_SOURCE_HEADER: Final[str] = "X-PLM-Data-Source"

LOOPBACK_ORIGIN_REGEX: Final[str] = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
"""Only browsers on this machine may call the API cross-origin."""


def _base_app(title: str, description: str, data_source: str) -> FastAPI:
    app = FastAPI(title=title, version="2.0.0", description=description)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=LOOPBACK_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", SERVICE_TOKEN_HEADER],
    )

    @app.middleware("http")
    async def stamp_data_source(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers[DATA_SOURCE_HEADER] = data_source
        return response

    return app


def _serve_static(app: FastAPI, directory: Path) -> None:
    app.mount("/static", StaticFiles(directory=str(directory)), name="static")
    index_file = directory / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def get_root() -> HTMLResponse:
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"), status_code=200)


# ================================================================================= live


class ResumeBody(BaseModel):
    actor: str
    role: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict)
    notes: str = ""


class RejectBody(BaseModel):
    actor: str
    role: str | None = None
    notes: str = ""


class ExternalResolutionBody(BaseModel):
    actor: str
    role: str | None = None
    outcome: str = "CLEARED"
    evidence: dict[str, str] = Field(default_factory=dict)
    notes: str = ""


class ResendSignalBody(BaseModel):
    actor: str


def _http(refused: ActionRefused) -> HTTPException:
    return HTTPException(status_code=refused.status_code, detail=refused.error.model_dump())


def create_live_app(
    live: LiveDashboard,
    actions: ExceptionActions,
    *,
    platform: Settings | None = None,
) -> FastAPI:
    """The live dashboard. ``platform=None`` keeps every mutating route closed (503)."""
    app = _base_app(
        "PLM Control Deck (live)",
        "The six PART XVII views over the PLM database and phase APIs, and the "
        "Exceptions actions over the R3 exception queue.",
        LIVE_DATA_SOURCE,
    )
    require_token = require_service_token(platform, surface="dashboard")

    @app.get("/api/views/{name}")
    def get_view(name: str) -> dict[str, Any]:
        if name not in VIEW_NAMES:
            raise HTTPException(status_code=404, detail=ErrorShape(
                code="unknown_view", field_paths=["name"],
                message=f"views are {list(VIEW_NAMES)}").model_dump())
        return live.view(name)

    @app.get("/api/exceptions/{case_id}")
    def get_exception(case_id: str) -> dict[str, Any]:
        try:
            detail = live.exception_detail(case_id)
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=ErrorShape(
                code="source_unavailable", field_paths=[exc.source],
                message=exc.reason).model_dump()) from None
        if detail is None:
            raise HTTPException(status_code=404, detail=ErrorShape(
                code="unknown_case", field_paths=["case_id"],
                message=f"no exception case {case_id!r}").model_dump())
        return detail

    @app.get("/api/reviewers")
    def get_reviewers() -> dict[str, Any]:
        reviewers = actions.reviewers
        return {"data_source": LIVE_DATA_SOURCE, "configured": reviewers is not None,
                "reviewers": [] if reviewers is None else reviewers.describe()}

    @app.post("/api/exceptions/{case_id}/resume", dependencies=[Depends(require_token)])
    async def resume(case_id: str, body: ResumeBody) -> dict[str, Any]:
        try:
            answer = await actions.resume(case_id, actor=body.actor, role=body.role,
                                          evidence=body.evidence, notes=body.notes)
        except ActionRefused as refused:
            raise _http(refused) from None
        return {"data_source": LIVE_DATA_SOURCE, "action": "resume", **answer}

    @app.post("/api/exceptions/{case_id}/reject", dependencies=[Depends(require_token)])
    async def reject(case_id: str, body: RejectBody) -> dict[str, Any]:
        try:
            answer = await actions.reject(case_id, actor=body.actor, role=body.role,
                                          notes=body.notes)
        except ActionRefused as refused:
            raise _http(refused) from None
        return {"data_source": LIVE_DATA_SOURCE, "action": "reject", **answer}

    @app.post("/api/exceptions/{case_id}/external-resolution",
              dependencies=[Depends(require_token)])
    async def external_resolution(case_id: str, body: ExternalResolutionBody) -> dict[str, Any]:
        try:
            answer = await actions.external_resolution(
                case_id, actor=body.actor, role=body.role, outcome=body.outcome,
                evidence=body.evidence, notes=body.notes)
        except ActionRefused as refused:
            raise _http(refused) from None
        return {"data_source": LIVE_DATA_SOURCE, "action": "external_resolution", **answer}

    @app.post("/api/pipeline/runs/{workflow_id}/signal", dependencies=[Depends(require_token)])
    async def resend_signal(workflow_id: str, body: ResendSignalBody) -> dict[str, Any]:
        try:
            answer = await actions.resend_signal(workflow_id, actor=body.actor)
        except ActionRefused as refused:
            raise _http(refused) from None
        return {"data_source": LIVE_DATA_SOURCE, "action": "resend_signal", **answer}

    _serve_static(app, LIVE_STATIC_DIR)
    return app


def build_live_components(
    platform: Settings | None,
    *,
    reviewers_file: str | Path | None,
    service_urls_env: dict[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    signaller: PipelineSignaller | None = None,
    clock: Clock | None = None,
) -> tuple[LiveDashboard, ExceptionActions]:
    """Wire the live dashboard from configuration.

    ``platform=None`` (settings unavailable) leaves the database, the phase APIs and
    Temporal unconfigured: every panel reports unavailable and every action is refused.
    A reviewer file that cannot be read or validated raises: a misconfigured directory
    is an error at startup, never an empty (or open) one.
    """
    from ..db import build_engine
    from ..service_urls import LOOPBACK_HOST, service_urls

    reviewers = None if reviewers_file is None else ReviewerConfig.from_json_file(reviewers_file)
    factory = None
    if platform is not None:
        factory = sessionmaker(bind=build_engine(platform))
    urls = service_urls(env=service_urls_env, default_host=LOOPBACK_HOST)
    token = None
    if platform is not None and platform.service_token is not None:
        token = platform.service_token.get_secret_value()
    database = ReadOnlyDatabase(factory)
    apis = PhaseApis(urls, token=token, transport=transport)
    live = LiveDashboard(database, apis, reviewers=reviewers, clock=clock)
    resolved_signaller = signaller
    if resolved_signaller is None and platform is not None:
        resolved_signaller = TemporalSignaller(platform)
    actions = ExceptionActions(factory, reviewers=reviewers, signaller=resolved_signaller,
                               clock=clock)
    return live, actions


# ================================================================================= demo


class PipelineRunRequest(BaseModel):
    mode: str = "full"


class QAResolveRequest(BaseModel):
    case_id: str
    resolution: str = "CLEARED_BY_OVERRIDE"
    notes: str = ""


def _items(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data_source": DEMO_DATA_SOURCE, "items": items}


def create_demo_app(
    service: DashboardService | None = None,
    orchestrator: DemoOrchestrator | None = None,
    *,
    platform: Settings | None = None,
) -> FastAPI:
    """The demo dashboard (``--demo``). ``platform=None`` keeps every mutating route closed."""
    app = _base_app(
        "PLM Control Deck (demo data)",
        "Fixed demo data and a scripted pipeline playback, served only behind --demo. "
        "It does not read or drive the pipeline.",
        DEMO_DATA_SOURCE,
    )
    srv = service or DashboardService()
    orch = orchestrator or DemoOrchestrator()
    require_token = require_service_token(platform, surface="dashboard")

    @app.get("/api/stats")
    def get_stats() -> dict[str, Any]:
        return {**srv.get_stats(), "data_source": DEMO_DATA_SOURCE}

    @app.get("/api/arms")
    def get_arms() -> dict[str, Any]:
        return _items(srv.get_arms())

    @app.get("/api/candidates")
    def get_candidates() -> dict[str, Any]:
        return _items(srv.get_candidates())

    @app.get("/api/audio/{candidate_id}")
    def get_audio(candidate_id: str) -> Response:
        wav_bytes = srv.generate_audio_wav(candidate_id)
        return Response(content=wav_bytes, media_type="audio/wav")

    @app.get("/api/releases")
    def get_releases() -> dict[str, Any]:
        return _items(srv.get_releases())

    @app.post("/api/pipeline/run", dependencies=[Depends(require_token)])
    async def run_pipeline(payload: PipelineRunRequest | None = None) -> dict[str, Any]:
        mode = payload.mode if payload else "full"
        run_id = await orch.start_run(mode=mode)
        return {"run_id": run_id, "status": "started", "mode": mode,
                "data_source": DEMO_DATA_SOURCE}

    @app.get("/api/pipeline/events")
    async def stream_events() -> StreamingResponse:
        async def event_generator() -> AsyncGenerator[str, None]:
            async for ev in orch.subscribe():
                yield f"data: {json.dumps(ev)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/qa/resolve", dependencies=[Depends(require_token)])
    def resolve_qa(payload: QAResolveRequest) -> dict[str, Any]:
        try:
            resolved = srv.resolve_qa_case(
                case_id=payload.case_id,
                resolution=payload.resolution,
                notes=payload.notes,
            )
        except UnknownCaseError:
            raise HTTPException(
                status_code=404,
                detail=ErrorShape(
                    code="unknown_case",
                    field_paths=["case_id"],
                    message=f"no quarantined case {payload.case_id!r}",
                ).model_dump(),
            ) from None
        return {**resolved, "data_source": DEMO_DATA_SOURCE}

    _serve_static(app, DEMO_STATIC_DIR)
    return app


def create_app(
    *,
    demo: bool = False,
    platform: Settings | None = None,
    reviewers_file: str | Path | None = None,
) -> FastAPI:
    """The dashboard the CLI serves: live unless ``demo`` is set explicitly."""
    if demo:
        return create_demo_app(platform=platform)
    live, actions = build_live_components(platform, reviewers_file=reviewers_file)
    return create_live_app(live, actions, platform=platform)

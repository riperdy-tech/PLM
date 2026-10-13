"""FastAPI application for the PLM visual dashboard (demo data only until R5).

Every response carries the ``X-PLM-Data-Source`` header and every JSON payload a
``data_source`` field, both set to :data:`~.service.DATA_SOURCE`. List endpoints return
``{"data_source": ..., "items": [...]}`` so the label travels with the data.

Mutating routes (``POST /api/pipeline/run``, ``POST /api/qa/resolve``) require the
platform service token and fail closed when none is configured (R15). Cross-origin access
is limited to loopback origins without credentials.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any, Final

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from plm_contracts import ErrorShape
from pydantic import BaseModel

from ..auth import require_service_token
from ..config import SERVICE_TOKEN_HEADER, Settings
from .orchestrator import DemoOrchestrator
from .service import DATA_SOURCE, DashboardService, UnknownCaseError

STATIC_DIR = Path(__file__).resolve().parent / "static"

DATA_SOURCE_HEADER: Final[str] = "X-PLM-Data-Source"

LOOPBACK_ORIGIN_REGEX: Final[str] = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
"""Only browsers on this machine may call the API cross-origin."""


class PipelineRunRequest(BaseModel):
    mode: str = "full"


class QAResolveRequest(BaseModel):
    case_id: str
    resolution: str = "CLEARED_BY_OVERRIDE"
    notes: str = ""


def _items(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data_source": DATA_SOURCE, "items": items}


def create_app(
    service: DashboardService | None = None,
    orchestrator: DemoOrchestrator | None = None,
    *,
    platform: Settings | None = None,
) -> FastAPI:
    """Build the dashboard app. ``platform=None`` keeps every mutating route closed."""
    app = FastAPI(
        title="PLM Control Deck (demo data)",
        version="1.0.0",
        description=(
            "Visual dashboard for the PLM engine. It currently serves fixed demo data and "
            "a scripted pipeline playback; it does not read or drive the pipeline."
        ),
    )

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
        response.headers[DATA_SOURCE_HEADER] = DATA_SOURCE
        return response

    srv = service or DashboardService()
    orch = orchestrator or DemoOrchestrator()
    require_token = require_service_token(platform, surface="dashboard")

    @app.get("/api/stats")
    def get_stats() -> dict[str, Any]:
        return {**srv.get_stats(), "data_source": DATA_SOURCE}

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
        return {"run_id": run_id, "status": "started", "mode": mode, "data_source": DATA_SOURCE}

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
        return {**resolved, "data_source": DATA_SOURCE}

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    index_file = STATIC_DIR / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def get_root() -> HTMLResponse:
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"), status_code=200)

    return app

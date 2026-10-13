"""FastAPI application for the PLM Visual Control Deck."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .orchestrator import PipelineOrchestrator
from .service import DashboardService

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    service: DashboardService | None = None,
    orchestrator: PipelineOrchestrator | None = None,
) -> FastAPI:
    """Build and configure the dashboard FastAPI instance."""
    app = FastAPI(
        title="PLM Autonomous Music Engine - Control Deck",
        version="1.0.0",
        description="Unified visual control and monitoring dashboard for the 9-phase PLM engine.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    srv = service or DashboardService()
    orch = orchestrator or PipelineOrchestrator()

    @app.get("/api/stats")
    def get_stats() -> dict[str, Any]:
        return srv.get_stats()

    @app.get("/api/arms")
    def get_arms() -> list[dict[str, Any]]:
        return srv.get_arms()

    @app.get("/api/candidates")
    def get_candidates() -> list[dict[str, Any]]:
        return srv.get_candidates()

    @app.get("/api/audio/{candidate_id}")
    def get_audio(candidate_id: str) -> Response:
        wav_bytes = srv.generate_audio_wav(candidate_id)
        return Response(content=wav_bytes, media_type="audio/wav")

    @app.get("/api/releases")
    def get_releases() -> list[dict[str, Any]]:
        return srv.get_releases()

    @app.post("/api/pipeline/run")
    async def run_pipeline(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        mode = payload.get("mode", "full")
        run_id = await orch.start_run(mode=mode)
        return {"run_id": run_id, "status": "started", "mode": mode}

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

    @app.post("/api/qa/resolve")
    def resolve_qa(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        case_id = payload.get("case_id")
        resolution = payload.get("resolution", "CLEARED_BY_OVERRIDE")
        notes = payload.get("notes", "")
        if not case_id:
            raise HTTPException(status_code=400, detail="case_id required")
        return srv.resolve_qa_case(case_id=case_id, resolution=resolution, notes=notes)

    # Static assets mounting
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        index_file.write_text(
            "<!DOCTYPE html><html><head><title>PLM Dashboard</title></head>"
            "<body><h1>PLM Autonomous Engine</h1></body></html>",
            encoding="utf-8",
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def get_root() -> HTMLResponse:
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"), status_code=200)
        return HTMLResponse("<h1>PLM Dashboard</h1>", status_code=200)

    return app

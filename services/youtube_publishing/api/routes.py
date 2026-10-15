"""FastAPI routes for `/youtube` publishing service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from plm_contracts import ErrorShape
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c6 import MediaPackagePayload
from plm_platform.clearance import ClearanceVerifier
from plm_platform.clock import Clock
from plm_platform.execution import ExternalWriteGuard

from ..client.mock import MockYouTubeClient
from ..domain.enums import OperatingMode
from ..engine import YouTubePublishingEngine
from ..manifests import PackageNotReadyError, build_publish_manifest, preflight_metadata
from ..preflight.gates import evaluate_preflight_all
from ..storage.repositories import (
    get_channel_profile_by_channel_id,
    get_publish_attempt_by_id,
    insert_publish_manifest,
)
from .schemas import (
    AttemptStatusResponse,
    HealthResponse,
    PublishRequest,
    PublishResponse,
    ReconcileResponse,
)


def create_youtube_router(
    session_factory: Any,
    auth_dependency: Any,
    client: Any = None,
    *,
    clock: Clock,
    clearance: ClearanceVerifier,
    write_guard: ExternalWriteGuard | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/youtube", tags=["youtube"])
    yt_client = client or MockYouTubeClient()
    guard = write_guard or ExternalWriteGuard()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            version="v1",
            timestamp=clock.now(),
        )

    @router.post("/publish", response_model=PublishResponse, dependencies=[Depends(auth_dependency)])
    def publish_package(req: PublishRequest) -> PublishResponse:
        now = clock.now()
        try:
            pkg = parse_seam(MediaPackagePayload, req.media_package)
        except SeamRejection as exc:
            raise HTTPException(
                status_code=422,
                detail=ErrorShape(
                    code=exc.error.code,
                    field_paths=[f"media_package.{p}" for p in exc.field_paths],
                    message=exc.error.message,
                ).model_dump(),
            ) from exc

        with session_factory() as session:
            # 1. The channel must be configured by an operator (P0 §10); never invented.
            profile = get_channel_profile_by_channel_id(session, req.channel_id)
            if not profile:
                raise HTTPException(
                    status_code=409,
                    detail=ErrorShape(
                        code="channel_not_configured",
                        field_paths=["channel_id"],
                        message=f"no channel profile for {req.channel_id!r}; an operator "
                        "must configure the channel and its OAuth credential first",
                    ).model_dump(),
                )

            # 2. Manifest from the package's facts, then preflight
            try:
                manifest = build_publish_manifest(
                    pkg, profile, publish_at=req.publish_at, now=now
                )
            except PackageNotReadyError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=ErrorShape(
                        code="package_not_ready",
                        field_paths=["media_package.status"],
                        message=str(exc),
                    ).model_dump(),
                ) from exc
            passed, results, reason = evaluate_preflight_all(
                profile, pkg.to_wire(), preflight_metadata(pkg), req.publish_at, now=now
            )
            if not passed:
                diags = [d for r in results for d in r.diagnostics]
                raise HTTPException(
                    status_code=422,
                    detail=ErrorShape(
                        code=reason.value if reason else "preflight_failed",
                        message="; ".join(diags),
                    ).model_dump(),
                )

            insert_publish_manifest(session, manifest)
            session.commit()

            # 4. Execute Publishing Engine
            engine = YouTubePublishingEngine(
                yt_client, clock=clock, clearance=clearance, write_guard=guard
            )
            result = engine.execute_publish(
                session,
                manifest,
                operating_mode=OperatingMode(req.operating_mode),
            )

            vid_id = result.attempt.youtube_video_id
            vid_url = result.publication_record.video_url if result.publication_record else None

            return PublishResponse(
                attempt_id=result.attempt.attempt_id,
                manifest_id=manifest.manifest_id,
                state=result.attempt.state.value,
                youtube_video_id=vid_id,
                video_url=vid_url,
                error=result.error,
                publication_event=result.event.to_wire() if result.event else None,
            )

    @router.get("/attempts/{attempt_id}", response_model=AttemptStatusResponse, dependencies=[Depends(auth_dependency)])
    def get_attempt(attempt_id: str) -> AttemptStatusResponse:
        with session_factory() as session:
            attempt = get_publish_attempt_by_id(session, attempt_id)
            if not attempt:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(code="attempt_not_found", message=f"Attempt {attempt_id} not found").model_dump(),
                )
            return AttemptStatusResponse(
                attempt_id=attempt.attempt_id,
                manifest_id=attempt.manifest_id,
                channel_id=attempt.channel_id,
                state=attempt.state.value,
                operating_mode=attempt.operating_mode.value,
                youtube_video_id=attempt.youtube_video_id,
                bytes_uploaded=attempt.bytes_uploaded,
                total_bytes=attempt.total_bytes,
                error_code=attempt.error_code,
                error_message=attempt.error_message,
                created_at=attempt.created_at,
                updated_at=attempt.updated_at,
                completed_at=attempt.completed_at,
            )

    @router.post("/reconcile/{attempt_id}", response_model=ReconcileResponse, dependencies=[Depends(auth_dependency)])
    def reconcile_attempt(attempt_id: str) -> ReconcileResponse:
        with session_factory() as session:
            attempt = get_publish_attempt_by_id(session, attempt_id)
            if not attempt:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(code="attempt_not_found", message=f"Attempt {attempt_id} not found").model_dump(),
                )
            if not attempt.youtube_video_id:
                raise HTTPException(
                    status_code=400,
                    detail=ErrorShape(code="not_uploaded", message=f"Attempt {attempt_id} has no remote video ID").model_dump(),
                )

            remote = yt_client.get_video(attempt.youtube_video_id) or {}
            return ReconcileResponse(
                attempt_id=attempt.attempt_id,
                state=attempt.state.value,
                youtube_video_id=attempt.youtube_video_id,
                remote_status=remote.get("status", {}),
            )

    return router

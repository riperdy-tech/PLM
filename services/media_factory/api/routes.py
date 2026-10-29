"""FastAPI router for Phase P6 (Playlist, Visual & Video Factory).

Notice: do NOT import annotations from __future__ here, as FastAPI depends on
runtime types for dependency injection with closures.
"""

import threading
from collections.abc import Callable, Iterator
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape
from plm_platform.clock import Clock
from sqlalchemy.orm import Session

from ..backend import MediaBackend
from ..config import MediaFactorySettings
from ..domain.enums import PackageStatus
from ..domain.models import LegacyPackageError, MediaPackage
from ..errors import MediaAssemblyError, MediaInfrastructureError
from ..service import assemble_and_persist
from ..storage.repositories import (
    get_media_package_by_id,
    get_playlist_session,
    get_render_job,
)
from .schemas import (
    AssemblePackageRequest,
    HealthResponse,
    MediaPackageResponse,
    PlaylistSessionResponse,
    RenderJobResponse,
)
from .service_token import require_service_token

__all__ = ["build_router"]


def _package_response(pkg: MediaPackage) -> MediaPackageResponse:
    qa = pkg.render_report.get("qa") or {}
    blocking = [] if pkg.status is PackageStatus.READY_FOR_PUBLISH else list(
        qa.get("reason_codes") or [])
    return MediaPackageResponse(
        media_package_id=pkg.media_package_id,
        release_manifest_id=pkg.release_manifest_id,
        experiment_id=pkg.experiment_id,
        content_arm_id=pkg.content_arm_id,
        brand_id=pkg.brand_id,
        playlist_session_id=pkg.playlist_session_id,
        visual_session_id=pkg.visual_session_id,
        video_master_uri=pkg.video_master_uri,
        video_checksum=pkg.video_checksum,
        video_duration_seconds=pkg.video_duration_seconds,
        render_profile=pkg.render_profile,
        audio_master_uri=pkg.audio_master_uri,
        audio_checksum=pkg.audio_checksum,
        thumbnails=pkg.thumbnails,
        tracklist=pkg.tracklist,
        chapters=pkg.chapters,
        metadata_candidates=pkg.metadata_candidates,
        short_source_assets=pkg.short_source_assets,
        render_report=pkg.render_report,
        status=pkg.status.value,
        package_version=pkg.package_version,
        blocking_reason_codes=blocking,
    )


def build_router(
    *,
    session_factory: Callable[[], Session] | None,
    settings: MediaFactorySettings,
    clock: Clock,
    backend: MediaBackend | None = None,
) -> APIRouter:
    """Build the authenticated router for the media factory."""
    router = APIRouter(prefix="/media", tags=["media_factory"])
    auth_dep = require_service_token(settings.platform)
    render_lock = threading.Lock()

    def _require_session() -> Iterator[Session]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=ErrorShape(
                    code="database_unavailable",
                    field_paths=[],
                    message="database session factory is not configured for media factory",
                ).model_dump(),
            )
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @router.get("/health", response_model=HealthResponse)
    def health_check() -> HealthResponse:
        """Health check endpoint without auth requirement."""
        return HealthResponse(status="ok", service="media_factory")

    @router.post(
        "/packages/assemble",
        response_model=MediaPackageResponse,
        dependencies=[Depends(auth_dep)],
    )
    def assemble_package_endpoint(
        payload: AssemblePackageRequest,
        session: Annotated[Session, Depends(_require_session)],
    ) -> MediaPackageResponse:
        """Assemble, render and package the recordings P5 cleared (R7, ADR-0028, R4).

        422: P6 refused the request (reason code). ``playlist_supply_short`` carries
        ``detail.facts.missing_seconds`` (how far the cleared supply is below the range
        minimum), so the caller tops the P3 package up and retries. 503: an authority,
        the artifact store or the toolchain is unavailable. 200 with ``status: failed``
        and ``blocking_reason_codes``: the render ran and post-render QA blocked it. A
        request that resolves to an existing package returns it without rendering.
        """
        if backend is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=ErrorShape(
                    code="media_backend_unavailable",
                    field_paths=[],
                    message="no clearance authority, artifact store or toolchain is configured",
                ).model_dump(),
            )
        try:
            # One render at a time per process, held until the package is committed: a
            # caller's retry after its read timeout (the render outlived the request)
            # then waits and replays the stored package instead of rendering it a second
            # time beside the first; concurrent long renders exhausted the container's
            # memory (R5). A durable render job is later work.
            with render_lock:
                package = assemble_and_persist(
                    session, backend, payload, clock=clock,
                    default_render_profile=settings.default_render_profile,
                    render_session_factory=session_factory,
                    worker_id_factory=lambda: uuid4().hex)
                session.commit()
        except MediaAssemblyError as exc:
            raise HTTPException(
                status_code=(status.HTTP_409_CONFLICT if exc.code in {
                    "render_job_reconciliation_required", "render_ownership_lost",
                    "render_input_changed",
                } else status.HTTP_422_UNPROCESSABLE_ENTITY),
                detail=exc.detail(),
            ) from exc
        except MediaInfrastructureError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.error.model_dump(),
            ) from exc
        return _package_response(package)

    @router.get(
        "/packages/{package_id}",
        response_model=MediaPackageResponse,
        dependencies=[Depends(auth_dep)],
    )
    def get_package_endpoint(
        package_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> MediaPackageResponse:
        """Retrieve an assembled media package by identifier."""
        pkg = get_media_package_by_id(session, package_id)
        if pkg is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorShape(
                    code="package_not_found",
                    field_paths=["package_id"],
                    message=f"media package {package_id} not found",
                ).model_dump(),
            )
        return _package_response(pkg)

    @router.get(
        "/packages/{package_id}/c6",
        dependencies=[Depends(auth_dep)],
    )
    def get_package_c6_endpoint(
        package_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        """Retrieve the frozen Seam C6 Hop 1 producer payload for downstream publishing."""
        pkg = get_media_package_by_id(session, package_id)
        if pkg is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorShape(
                    code="package_not_found",
                    field_paths=["package_id"],
                    message=f"media package {package_id} not found",
                ).model_dump(),
            )
        try:
            return pkg.to_c6_payload()
        except LegacyPackageError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorShape(
                    code="legacy_media_package", field_paths=["package_id"], message=str(exc)
                ).model_dump(),
            ) from exc

    @router.get(
        "/playlists/{playlist_id}",
        response_model=PlaylistSessionResponse,
        dependencies=[Depends(auth_dep)],
    )
    def get_playlist_endpoint(
        playlist_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> PlaylistSessionResponse:
        """Retrieve a playlist session and sequenced tracks."""
        pl = get_playlist_session(session, playlist_id)
        if pl is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorShape(
                    code="playlist_not_found",
                    field_paths=["playlist_id"],
                    message=f"playlist session {playlist_id} not found",
                ).model_dump(),
            )
        tracks_data = [
            {
                "id": t.id,
                "song_id": t.song_id,
                "position": t.position,
                "start_offset_seconds": t.start_offset_seconds,
                "end_offset_seconds": t.end_offset_seconds,
                "transition_type": t.transition_type.value,
                "title": t.title,
                "audio_uri": t.audio_uri,
            }
            for t in pl.tracks
        ]
        return PlaylistSessionResponse(
            id=pl.id,
            release_manifest_id=pl.release_manifest_id,
            experiment_id=pl.experiment_id,
            content_arm_id=pl.content_arm_id,
            brand_id=pl.brand_id,
            status=pl.status.value,
            requested_duration_seconds=pl.requested_duration_seconds,
            actual_duration_seconds=pl.actual_duration_seconds,
            sequence_version=pl.sequence_version,
            sequence_checksum=pl.sequence_checksum,
            tracks=tracks_data,
        )

    @router.get(
        "/renders/{render_job_id}",
        response_model=RenderJobResponse,
        dependencies=[Depends(auth_dep)],
    )
    def get_render_job_endpoint(
        render_job_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> RenderJobResponse:
        """Retrieve a render job execution record."""
        rj = get_render_job(session, render_job_id)
        if rj is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorShape(
                    code="render_job_not_found",
                    field_paths=["render_job_id"],
                    message=f"render job {render_job_id} not found",
                ).model_dump(),
            )
        return RenderJobResponse(
            id=rj.id,
            media_package_id=rj.media_package_id,
            render_profile=rj.render_profile,
            render_version=rj.render_version,
            job_status=rj.job_status.value,
            priority=rj.priority,
            attempt_count=rj.attempt_count,
            worker_id=rj.worker_id,
            output_checksum=rj.output_checksum,
            error_code=rj.error_code,
            error_detail=rj.error_detail,
        )

    return router

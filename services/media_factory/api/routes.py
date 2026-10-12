"""FastAPI router for Phase P6 (Playlist, Visual & Video Factory).

Notice: do NOT import annotations from __future__ here, as FastAPI depends on
runtime types for dependency injection with closures.
"""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape
from sqlalchemy.orm import Session

from ..config import MediaFactorySettings
from ..rendering.assembler import assemble_media_package
from ..storage.repositories import (
    get_media_package_by_id,
    get_playlist_session,
    get_render_job,
    insert_media_package,
    insert_playlist_session,
    insert_playlist_track,
    insert_render_job,
    insert_thumbnail_variant,
    insert_visual_asset,
    insert_visual_session,
)
from ..visuals.planner import BrandProfile
from .schemas import (
    AssemblePackageRequest,
    HealthResponse,
    MediaPackageResponse,
    PlaylistSessionResponse,
    RenderJobResponse,
)
from .service_token import require_service_token

__all__ = ["build_router"]


def build_router(
    *,
    session_factory: Callable[[], Session] | None,
    settings: MediaFactorySettings,
) -> APIRouter:
    """Build the authenticated router for the media factory."""
    router = APIRouter(prefix="/media", tags=["media_factory"])
    auth_dep = require_service_token(settings.platform)

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
        """Assemble a complete publish-ready media package from a P5 release manifest."""
        brand = BrandProfile(
            brand_id=payload.brand_id,
            brand_name=payload.brand_name,
        )

        try:
            result = assemble_media_package(
                release_manifest=payload.release_manifest,
                brand_profile=brand,
                render_profile=payload.render_profile,
                required_duration_seconds=payload.required_duration_seconds,
                sequence_version=payload.sequence_version,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ErrorShape(
                    code="assembly_validation_failed",
                    field_paths=["release_manifest"],
                    message=str(exc),
                ).model_dump(),
            )

        # Persist session, tracks, visual session, assets, thumbnails, render job, and package
        insert_playlist_session(session, result.playlist_session)
        for tr in result.playlist_tracks:
            insert_playlist_track(session, tr)

        insert_visual_session(session, result.visual_session)
        for va in result.visual_assets:
            insert_visual_asset(session, va)

        for tv in result.thumbnail_variants:
            insert_thumbnail_variant(session, tv)

        insert_render_job(session, result.render_job)
        insert_media_package(session, result.media_package)

        pkg = result.media_package
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
        )

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
        )

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
        return pkg.to_c6_payload()

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

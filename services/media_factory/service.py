"""One assembly entry point shared by the P6 API and CLI (ADR-0028, R4).

:func:`assemble_and_persist` resolves the brand and render profile, fetches the cleared
C5 manifests from their authority, assembles and renders, and persists every record in
the caller's session. It raises :class:`~.errors.MediaAssemblyError` (a refusal) or
:class:`~.errors.MediaInfrastructureError` (retryable); a request that resolves to an
existing package returns that package without rendering.
"""

from __future__ import annotations

from plm_platform.clock import Clock
from sqlalchemy.orm import Session

from .api.schemas import AssemblePackageRequest
from .backend import MediaBackend
from .domain.models import MediaPackage
from .playlist.intake import fetch_cleared_manifests
from .rendering.assembler import PackageAlreadyAssembled, assemble_media_package
from .rendering.profiles import render_profile
from .storage.repositories import (
    get_media_package_by_id,
    insert_media_package,
    insert_playlist_session,
    insert_playlist_track,
    insert_render_job,
    insert_thumbnail_variant,
    insert_visual_asset,
    insert_visual_session,
)

__all__ = ["assemble_and_persist"]


def assemble_and_persist(session: Session, backend: MediaBackend,
                         request: AssemblePackageRequest, *, clock: Clock) -> MediaPackage:
    brand = backend.brands.get(request.brand_id)
    profile = render_profile(request.render_profile)
    manifests = fetch_cleared_manifests(
        request.candidate_ids, manifests=backend.manifests, holds=backend.holds)
    try:
        result = assemble_media_package(
            track_manifests=manifests,
            brand_profile=brand,
            duration_range=request.duration_range_seconds,
            visual_profile=request.visual_profile,
            playlist_concept=request.playlist_concept,
            store=backend.store,
            toolchain=backend.toolchain,
            render_profile_name=profile,
            transition_type=request.transition_type,
            transition_duration_seconds=request.transition_duration_seconds,
            existing=lambda package_id: get_media_package_by_id(session, package_id),
            clock=clock,
        )
    except PackageAlreadyAssembled as replay:
        return replay.package

    insert_playlist_session(session, result.playlist_session)
    for track in result.playlist_tracks:
        insert_playlist_track(session, track)
    insert_visual_session(session, result.visual_session)
    for asset in result.visual_assets:
        insert_visual_asset(session, asset)
    for thumbnail in result.thumbnail_variants:
        insert_thumbnail_variant(session, thumbnail)
    insert_render_job(session, result.render_job)
    insert_media_package(session, result.media_package)
    return result.media_package

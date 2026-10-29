"""One assembly entry point shared by the P6 API and CLI (ADR-0028, R4).

:func:`assemble_and_persist` resolves the brand and render profile, fetches the cleared
C5 manifests from their authority, assembles and renders, and persists every record in
the caller's session. It raises :class:`~.errors.MediaAssemblyError` (a refusal) or
:class:`~.errors.MediaInfrastructureError` (retryable); a request that resolves to an
existing package returns that package without rendering.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from plm_platform.clock import Clock
from sqlalchemy.orm import Session

from .api.schemas import AssemblePackageRequest
from .backend import MediaBackend
from .domain.enums import RenderJobStatus
from .domain.models import MediaPackage, RenderJob
from .errors import MediaAssemblyError
from .playlist.intake import fetch_cleared_manifests
from .rendering.assembler import PackageAlreadyAssembled, assemble_media_package
from .rendering.profiles import render_profile
from .rendering.renderer import RENDER_VERSION
from .storage.render_claims import (
    claim_queued_render_job,
    finish_claimed_render_job,
    queue_render_job,
    render_claim_namespace,
)
from .storage.repositories import (
    get_media_package_by_id,
    get_playlist_session,
    get_render_job,
    get_visual_session,
    insert_media_package,
    insert_playlist_session,
    insert_playlist_track,
    insert_render_job,
    insert_thumbnail_variant,
    insert_visual_asset,
    insert_visual_session,
    list_catalog_albums,
    list_visual_assets,
)

__all__ = ["assemble_and_persist"]


def assemble_and_persist(session: Session, backend: MediaBackend,
                         request: AssemblePackageRequest, *, clock: Clock,
                         default_render_profile: str,
                         render_session_factory: Callable[[], Session] | None = None,
                         worker_id_factory: Callable[[], str] | None = None,
                         recovery_job_id: str | None = None,
                         ) -> MediaPackage:
    """Assemble and persist, optionally committing a fenced intent before rendering.

    The P6 HTTP and CLI entry points supply a separate DB session factory
    to commit QUEUED and RUNNING ahead of FFmpeg. Direct in-process callers
    without a factory retain the legacy path. A crash leaves RUNNING for
    explicit reconciliation, never automatic replay or blob publication.
    """
    claim: tuple[str, str, str, datetime] | None = None
    recovery_attempt = 1
    if recovery_job_id is not None:
        # No HTTP/CLI exposes this option. Only an independently supervised
        # caller holding a previously staged retry intent may use it.
        if render_session_factory is None:
            raise ValueError("recovery requires a committed render session factory")
        with render_session_factory() as lookup_session:
            approved = get_render_job(lookup_session, recovery_job_id)
            if (approved is None or approved.job_status is not RenderJobStatus.QUEUED
                    or approved.attempt_count <= 1 or approved.worker_id is not None
                    or approved.started_at is not None):
                raise MediaAssemblyError(
                    "render_retry_not_prepared",
                    "Recovery needs a separate, previously approved queued attempt",
                )
            recovery_attempt = approved.attempt_count

    def _before_render(package_id: str, profile_name: str, input_hash: str) -> str:
        nonlocal claim
        if render_session_factory is None:
            raise ValueError("render claiming requires an independent DB session factory")
        if worker_id_factory is None:
            raise ValueError("render claiming requires an edge-provided worker ID factory")
        started = clock.now()
        worker_id = worker_id_factory()
        if not worker_id or not worker_id.strip():
            raise ValueError("render worker ID must not be blank")
        if recovery_job_id is None:
            queued = RenderJob.create(
                package_id, profile_name, render_version=RENDER_VERSION,
                input_manifest_hash=input_hash, now=started,
            )
            # Commit the new job intent independently before any FFmpeg work.
            with render_session_factory() as intent_session:
                queue_render_job(intent_session, queued)
                intent_session.commit()
        else:
            # A retry is NOT implicitly authorized by the assembly request.
            # Re-read the pre-queued job and match the newly computed inputs.
            with render_session_factory() as intent_session:
                recovered = get_render_job(intent_session, recovery_job_id)
            if (recovered is None or recovered.job_status is not RenderJobStatus.QUEUED
                    or recovered.attempt_count != recovery_attempt
                    or recovered.media_package_id != package_id
                    or recovered.render_profile != profile_name
                    or recovered.render_version != RENDER_VERSION
                    or recovered.input_manifest_hash != input_hash
                    or recovered.worker_id is not None or recovered.started_at is not None):
                raise MediaAssemblyError(
                    "render_retry_input_changed",
                    "The staged retry no longer matches the reverified render input",
                )
            queued = recovered
        with render_session_factory() as claim_session:
            won = claim_queued_render_job(
                claim_session, job_id=queued.id, worker_id=worker_id,
                input_manifest_hash=input_hash, started_at=started,
            )
            if not won:
                raise MediaAssemblyError(
                    "render_job_reconciliation_required",
                    "Render job is already running or terminal; inspect its state before retry",
                    ["candidate_ids"],
                )
            claim_session.commit()
        claim = (queued.id, worker_id, input_hash, started)
        # Bind all provisional render and thumbnail keys to this committed
        # execution, not merely the package ID. An orphan left by a killed
        # worker cannot share its output prefix with another claimed run.
        # The pure function can be reused to locate this execution's orphan
        # blobs from its durable claim, without exposing the worker token.
        return render_claim_namespace(
            job_id=queued.id, worker_id=worker_id,
            input_manifest_hash=input_hash, started_at=started,
        )

    brand = backend.brands.get(request.brand_id)
    profile = render_profile(request.render_profile or default_render_profile)
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
            catalog_loader=lambda: list_catalog_albums(session, request.brand_id),
            before_render=_before_render if render_session_factory is not None else None,
            render_attempt=recovery_attempt,
            clock=clock,
        )
    except PackageAlreadyAssembled as replay:
        return replay.package

    # The sequence and the visual plan do not depend on the render profile, so the same
    # recordings rendered under another profile (a new package) share them: they are
    # stored once, and a stored sequence that differs is a conflict, never overwritten.
    stored = get_playlist_session(session, result.playlist_session.id)
    if stored is None:
        insert_playlist_session(session, result.playlist_session)
        for track in result.playlist_tracks:
            insert_playlist_track(session, track)
    elif stored.sequence_checksum != result.playlist_session.sequence_checksum:
        raise MediaAssemblyError(
            "playlist_session_conflict",
            f"playlist session {stored.id} is stored with another sequence", ["candidate_ids"])
    if get_visual_session(session, result.visual_session.id) is None:
        insert_visual_session(session, result.visual_session)
    known_assets = {a.id for a in list_visual_assets(session, result.visual_session.id)}
    for asset in result.visual_assets:
        if asset.id not in known_assets:
            insert_visual_asset(session, asset)
    for thumbnail in result.thumbnail_variants:
        insert_thumbnail_variant(session, thumbnail)
    if claim is None:
        insert_render_job(session, result.render_job)
    else:
        job_id, worker_id, input_hash, started_at = claim
        completed = result.render_job
        if completed.id != job_id or completed.input_manifest_hash != input_hash:
            raise MediaAssemblyError(
                "render_input_changed",
                "The renderer produced an output for a different input manifest",
            )
        if not finish_claimed_render_job(
            session, job_id=job_id, worker_id=worker_id,
            input_manifest_hash=input_hash, started_at=started_at,
            finished_at=clock.now(), status=completed.job_status,
            output_checksum=(completed.output_checksum if
                             completed.job_status is RenderJobStatus.COMPLETED else None),
            error_code=completed.error_code, error_detail=completed.error_detail,
        ):
            raise MediaAssemblyError(
                "render_ownership_lost",
                "Render claim was fenced; output must not become a publishable package",
            )
    # The fenced job completion and package publication commit atomically.
    insert_media_package(session, result.media_package)
    return result.media_package

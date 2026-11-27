"""One assembly entry point shared by the P6 API and CLI (ADR-0028, R4).

:func:`assemble_and_persist` resolves the brand and render profile, fetches the cleared
C5 manifests from their authority, assembles and renders, and persists every record in
the caller's session. It raises :class:`~.errors.MediaAssemblyError` (a refusal) or
:class:`~.errors.MediaInfrastructureError` (retryable); a request that resolves to an
existing package returns that package without rendering.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from plm_platform.clock import Clock
from sqlalchemy.orm import Session

from .api.schemas import AssemblePackageRequest
from .artifacts import ClaimListingStore
from .backend import MediaBackend
from .domain.enums import PackageStatus, RenderJobStatus
from .domain.models import MediaPackage, RenderJob
from .errors import MediaAssemblyError
from .playlist.intake import fetch_cleared_manifests
from .render_reconciliation import inspect_render_claim, record_claim_inventory
from .rendering.assembler import (
    AssemblyPipelineResult,
    PackageAlreadyAssembled,
    assemble_media_package,
)
from .rendering.profiles import render_profile
from .rendering.renderer import RENDER_VERSION
from .storage.render_claims import (
    RecoveryEvidenceKind,
    claim_queued_render_job,
    finish_claimed_render_job,
    list_render_job_summaries,
    quarantine_stale_render_job,
    queue_reconciled_render_retry,
    queue_render_job,
    record_render_recovery_evidence,
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
from .thumbnail_art.guide import build_prompts, load_style_guide
from .thumbnail_art.inbox import write_job

__all__ = ["assemble_and_persist", "retry_render"]

logger = logging.getLogger(__name__)


def assemble_and_persist(session: Session, backend: MediaBackend,
                         request: AssemblePackageRequest, *, clock: Clock,
                         default_render_profile: str,
                         render_session_factory: Callable[[], Session] | None = None,
                         worker_id_factory: Callable[[], str] | None = None,
                         recovery_job_id: str | None = None,
                         thumbnail_art_inbox: Path | None = None,
                         ) -> MediaPackage:
    """Assemble and persist, optionally committing a fenced intent before rendering.

    The P6 HTTP and CLI entry points supply a separate DB session factory
    to commit QUEUED and RUNNING ahead of FFmpeg. Direct in-process callers
    without a factory retain the legacy path. A crash leaves RUNNING for
    explicit reconciliation, never automatic replay or blob publication.

    With ``thumbnail_art_inbox`` a package that passed QA waits at
    ``awaiting_thumbnail_art`` instead of offering P6's drawn thumbnails, and its art
    job is written to the inbox (:mod:`.thumbnail_art`).
    """
    claim: tuple[str, str, str, datetime] | None = None
    recovery_attempt = 1
    if recovery_job_id is not None:
        # Only a caller holding a previously staged retry intent may use it
        # (:func:`retry_render`, behind ``plm media retry-render`` and its route).
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
                try:
                    queue_render_job(intent_session, queued)
                except ValueError as exc:
                    # An earlier attempt of this package was queued with other inputs
                    # (a changed concept, a new render version): a refusal, not a 500.
                    raise MediaAssemblyError(
                        "render_intent_conflict",
                        f"render job {queued.id} was queued earlier with other inputs: {exc}",
                    ) from exc
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
                    # The caller retries a failed render by this id (retry-render, #22).
                    facts={"media_package_id": package_id},
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
            session_compilation=request.session_compilation,
            subgenre=request.subgenre,
            # The owner's art replaces P6's thumbnails at import: draw none for the inbox.
            drawn_thumbnails=thumbnail_art_inbox is None,
            clock=clock,
        )
    except PackageAlreadyAssembled as replay:
        # The package id does not include the visual world: the same songs asked for in
        # another world would otherwise replay the stored package with the old one.
        stored_world = (replay.package.render_report.get("variation") or {}).get(
            "visual_world")
        if stored_world is not None and stored_world != request.visual_profile:
            raise MediaAssemblyError(
                "package_visual_profile_conflict",
                f"{replay.package.media_package_id} was assembled in the visual world "
                f"{stored_world!r}, not {request.visual_profile!r}; the same songs cannot "
                "be packaged again in another world", ["visual_profile"]) from None
        _write_art_job(replay.package, request, thumbnail_art_inbox)
        return replay.package
    except Exception as exc:
        if claim is not None and render_session_factory is not None:
            _quarantine_own_claim(render_session_factory, claim, exc, clock)
        raise

    if (thumbnail_art_inbox is not None
            and result.media_package.status is PackageStatus.READY_FOR_PUBLISH):
        result.media_package.status = PackageStatus.AWAITING_THUMBNAIL_ART
    try:
        persisted = _persist_assembled(session, result, claim, clock)
    except Exception as exc:
        # The render finished but this process could not or would not persist
        # it. Fence the claim unless another actor already did (ownership lost).
        if (claim is not None and render_session_factory is not None
                and getattr(exc, "code", None) != "render_ownership_lost"):
            # This request is failing anyway. Release its uncommitted writes first:
            # the fenced completion UPDATE holds the job row lock, and fencing from
            # another session while it is held would wait on this thread forever.
            session.rollback()
            _quarantine_own_claim(render_session_factory, claim, exc, clock)
        raise
    # Outside the fenced block and never fatal: a raise here would roll back the fenced
    # completion with the caller's transaction and leave the claim RUNNING for good. A
    # replay of the same request (or ``plm media thumbnail-job``) writes the job later.
    try:
        _write_art_job(persisted, request, thumbnail_art_inbox)
    except Exception as exc:  # noqa: BLE001 - the package is rendered; only the job is late
        logger.warning("art job for %s not written (%s: %s); replay the request or run "
                       "`plm media thumbnail-job` to write it", persisted.media_package_id,
                       type(exc).__name__, exc)
    return persisted


def retry_render(session: Session, backend: MediaBackend, request: AssemblePackageRequest,
                 *, package_id: str, clock: Clock, default_render_profile: str,
                 render_session_factory: Callable[[], Session],
                 worker_id_factory: Callable[[], str], recorded_by: str,
                 orphan_evidence_ref: str | None = None,
                 thumbnail_art_inbox: Path | None = None) -> MediaPackage:
    """Render a package again after its claimed render failed (ADR-0043, review #22).

    A failed render leaves its claim QUARANTINED, and assembling the same request again
    is refused (``render_job_reconciliation_required``). This stages the next attempt and
    runs it: the failed claim's output prefix is listed and recorded as its
    ``orphan_artifacts`` evidence (or ``orphan_evidence_ref`` is recorded as the
    operator's attestation when nothing was listed, for example a render that failed
    before writing anything), :func:`queue_reconciled_render_retry` stages the attempt,
    and :func:`assemble_and_persist` runs it with ``recovery_job_id``. ``request`` must be
    the package's own assembly request: a request that resolves to another package or
    other inputs is refused before rendering, and the staged attempt is kept for the
    right one. A claim still RUNNING needs ``render-attest-termination`` first.
    """
    if not recorded_by.strip():
        raise MediaAssemblyError("render_retry_not_prepared", "name who asks for the retry")
    with render_session_factory() as lookup:
        if get_media_package_by_id(lookup, package_id) is not None:
            raise MediaAssemblyError(
                "render_retry_package_exists",
                f"{package_id} is already assembled; nothing to render again")
        history = list_render_job_summaries(lookup, media_package_id=package_id, limit=100)
        latest = (get_render_job(lookup, max(history, key=lambda j: j.attempt_count).job_id)
                  if history else None)
    if latest is None:
        raise MediaAssemblyError("render_job_unknown",
                                 f"{package_id} has no render job to retry")
    if (latest.job_status is RenderJobStatus.QUEUED and latest.attempt_count > 1
            and latest.worker_id is None and latest.started_at is None):
        staged = latest  # staged earlier; its run never claimed it
    elif latest.job_status is RenderJobStatus.QUARANTINED and latest.worker_id is not None \
            and latest.started_at is not None:
        staged = _stage_retry(render_session_factory, backend, latest,
                              brand_id=request.brand_id, recorded_by=recorded_by.strip(),
                              orphan_evidence_ref=orphan_evidence_ref, clock=clock)
    else:
        raise MediaAssemblyError(
            "render_job_reconciliation_required",
            f"render job {latest.id} is {latest.job_status.value}; only a quarantined "
            "render can be retried (a RUNNING one needs render-attest-termination first)")
    return assemble_and_persist(
        session, backend, request, clock=clock,
        default_render_profile=default_render_profile,
        render_session_factory=render_session_factory,
        worker_id_factory=worker_id_factory, recovery_job_id=staged.id,
        thumbnail_art_inbox=thumbnail_art_inbox)


def _stage_retry(render_session_factory: Callable[[], Session], backend: MediaBackend,
                 failed: RenderJob, *, brand_id: str, recorded_by: str,
                 orphan_evidence_ref: str | None, clock: Clock) -> RenderJob:
    assert failed.worker_id is not None and failed.started_at is not None
    now = max(clock.now(), failed.finished_at or failed.started_at)
    with render_session_factory() as stage:
        try:
            if orphan_evidence_ref is not None and orphan_evidence_ref.strip():
                record_render_recovery_evidence(
                    stage, job_id=failed.id, kind=RecoveryEvidenceKind.ORPHAN_ARTIFACTS,
                    method="supervisor_attestation", worker_id=failed.worker_id,
                    started_at=failed.started_at,
                    input_manifest_hash=failed.input_manifest_hash,
                    recorded_by=recorded_by, recorded_at=now,
                    evidence_ref=orphan_evidence_ref, detail={})
            elif isinstance(backend.store, ClaimListingStore):
                inspection = inspect_render_claim(stage, backend.store, job_id=failed.id,
                                                  brand_id=brand_id, limit=100)
                record_claim_inventory(stage, inspection, recorded_by=recorded_by,
                                       recorded_at=now)
            else:
                raise ValueError("the artifact store cannot list the failed render's output")
        except ValueError as exc:
            raise MediaAssemblyError(
                "render_retry_not_prepared",
                f"the failed render {failed.id}'s output could not be inventoried ({exc}); "
                "check its prefix by hand and pass the evidence reference") from exc
        staged = queue_reconciled_render_retry(
            stage, previous_job_id=failed.id, expected_worker_id=failed.worker_id,
            expected_started_at=failed.started_at,
            input_manifest_hash=failed.input_manifest_hash, now=now)
        if staged is None:
            raise MediaAssemblyError(
                "render_retry_not_allowed",
                f"no further attempt can follow {failed.id} (attempt "
                f"{failed.attempt_count}; at most 3, and none after a stored package)")
        stage.commit()
    return staged


def _write_art_job(pkg: MediaPackage, request: AssemblePackageRequest,
                   inbox: Path | None) -> None:
    """Write a waiting package's art job (once; a replay finds it already there)."""
    if inbox is None or pkg.status is not PackageStatus.AWAITING_THUMBNAIL_ART:
        return
    guide = load_style_guide()
    write_job(inbox, package_id=pkg.media_package_id,
              title=str(pkg.metadata_candidates[0]["title"]),
              guide_version=guide.guide_version,
              prompts=build_prompts(guide, package_id=pkg.media_package_id,
                                    thumbnail_family=request.thumbnail_family,
                                    visual_world=request.visual_profile))


def _persist_assembled(session: Session, result: AssemblyPipelineResult,
                       claim: tuple[str, str, str, datetime] | None,
                       clock: Clock) -> MediaPackage:
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


def _quarantine_own_claim(render_session_factory: Callable[[], Session],
                          claim: tuple[str, str, str, datetime], exc: Exception,
                          clock: Clock) -> None:
    """Owner-observed exit (ADR-0043): record it and fence this claim.

    This process owns the claim and its render call has already returned or
    raised; ``subprocess.run`` reaps FFmpeg before either, so no child of this
    execution can still write. A supervisor then finds QUARANTINED with
    evidence instead of an ambiguous RUNNING row. A killed process never
    reaches this code; its claim still needs out-of-band termination evidence.
    The original failure is always re-raised by the caller, never masked.
    """
    job_id, worker_id, input_hash, started_at = claim
    now = clock.now()
    try:
        with render_session_factory() as fence_session:
            record_render_recovery_evidence(
                fence_session, job_id=job_id,
                kind=RecoveryEvidenceKind.WORKER_TERMINATION,
                method="owner_exit_observed", worker_id=worker_id,
                started_at=started_at, input_manifest_hash=input_hash,
                recorded_by=worker_id, recorded_at=now,
                detail={"exception_type": type(exc).__name__,
                        "error_code": getattr(exc, "code", None)},
            )
            quarantine_stale_render_job(
                fence_session, job_id=job_id, input_manifest_hash=input_hash,
                started_before=now, quarantined_at=now,
                expected_worker_id=worker_id, expected_started_at=started_at,
            )
            fence_session.commit()
    except Exception as fence_error:  # noqa: BLE001 - the render failure is what propagates
        exc.add_note(f"render claim {job_id} left RUNNING: owner fence failed "
                     f"({type(fence_error).__name__})")

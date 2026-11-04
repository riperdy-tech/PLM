"""CLI entry points and commands for Playlist, Visual & Video Factory service (P6 §45)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import typer
import uvicorn
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_platform.clock import SystemClock
from plm_platform.config import load_settings
from plm_platform.db import session_factory as platform_session_factory

from .api.schemas import AssemblePackageRequest
from .artifacts import MinioArtifactStore
from .backend import deployed_backend
from .config import load_media_factory_settings
from .domain.enums import PackageStatus, RenderJobStatus
from .errors import MediaAssemblyError, MediaInfrastructureError
from .render_reconciliation import inspect_render_claim, record_claim_inventory
from .rendering.ffmpeg import FFmpegToolchain
from .service import assemble_and_persist
from .storage.render_claims import (
    RecoveryEvidenceKind,
    list_render_job_summaries,
    quarantine_stale_render_job,
    record_render_recovery_evidence,
)
from .storage.repositories import (
    get_media_package_by_id,
    get_render_job,
    list_catalog_albums,
)
from .texture_backfill import backfill_package_texture, backfill_packages_texture
from .texture_inventory import inspect_brand_album_textures
from .thumbnail_art.guide import build_prompts, load_style_guide
from .thumbnail_art.importer import import_owner_art
from .thumbnail_art.inbox import INBOX_VAR, write_job

media_cli = typer.Typer(help="Media factory CLI commands")


@media_cli.command(name="assemble")
def cli_assemble(
    request_path: str = typer.Option(
        ...,
        "--request",
        "-r",
        help="Path to an assembly request JSON: candidate_ids, brand_id, "
        "duration_range_seconds {min_seconds, max_seconds}, visual_profile, "
        "playlist_concept (render_profile and transition optional)",
    ),
) -> None:
    """Assemble and render a media package from recordings P5 cleared (ADR-0028, R4).

    The same path as ``POST /media/packages/assemble``: manifests are fetched from P5 by
    candidate id, audio from the artifact store, and the render runs on FFmpeg.
    """
    path = Path(request_path)
    if not path.is_file():
        typer.echo(f"Error: request file {request_path} does not exist.", err=True)
        raise typer.Exit(code=1)
    try:
        req = AssemblePackageRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        typer.echo(f"Error: invalid request: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _run_assembly(req)


def _run_assembly(req: AssemblePackageRequest) -> None:
    platform = load_settings()
    factory = platform_session_factory()
    backend = deployed_backend(platform, factory)
    if backend is None:
        typer.echo("blocked: no service token (PLM_SERVICE_TOKEN) to ask P5", err=True)
        raise typer.Exit(code=3)
    settings = load_media_factory_settings(platform=platform)
    with factory() as session:
        try:
            pkg = assemble_and_persist(
                session, backend, req, clock=SystemClock(),
                default_render_profile=settings.default_render_profile,
                render_session_factory=factory,
                worker_id_factory=lambda: uuid4().hex,
                thumbnail_art_inbox=settings.thumbnail_art_inbox)
        except MediaAssemblyError as exc:
            typer.echo(f"Error: assembly refused: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        except MediaInfrastructureError as exc:
            typer.echo(f"Error: assembly unavailable: {exc}", err=True)
            raise typer.Exit(code=3) from exc
        session.commit()

    typer.echo(f"Media Package ID:    {pkg.media_package_id}")
    typer.echo(f"Release Manifest ID: {pkg.release_manifest_id}")
    typer.echo(f"Video Master:        {pkg.video_master_uri}")
    typer.echo(f"Video Checksum:      {pkg.video_checksum}")
    typer.echo(f"Duration:            {pkg.video_duration_seconds:.2f}s")
    typer.echo(f"Thumbnails:          {len(pkg.thumbnails)} variants")
    typer.echo(f"Chapters:            {len(pkg.chapters)}")
    typer.echo(f"Status:              {pkg.status.value}")
    if (pkg.status is PackageStatus.AWAITING_THUMBNAIL_ART
            and settings.thumbnail_art_inbox is not None):
        typer.echo("Waiting for thumbnail art: follow "
                   f"{settings.thumbnail_art_inbox / pkg.media_package_id / 'PROMPTS.md'}")
        return
    if pkg.status is not PackageStatus.READY_FOR_PUBLISH:
        codes = (pkg.render_report.get("qa") or {}).get("reason_codes") or []
        typer.echo(f"Blocked by post-render QA: {', '.join(codes)}", err=True)
        raise typer.Exit(code=2)


@media_cli.command(name="compile-session")
def cli_compile_session(
    brand_id: str = typer.Option(..., "--brand-id", help="Brand whose released tracks to reuse"),
    min_minutes: int = typer.Option(60, "--min-minutes", min=10),
    max_minutes: int = typer.Option(120, "--max-minutes", min=10),
    visual_profile: str = typer.Option(
        ..., "--visual-profile", help="Visual world of the session video (P6 worlds)"),
    playlist_concept: str = typer.Option(
        "Deep Work Session", "--concept", help="Concept the title is built from"),
    albums: int = typer.Option(
        12, "--albums", min=1, max=100, help="How many recent released albums to draw from"),
) -> None:
    """Assemble a long session video from tracks this brand already released (ADR-0044).

    Offers every track of the brand's most recent ready or published albums; P6's
    sequencer picks and orders them to land inside the range. The video takes its own
    ``session_compilation`` lineage, so its results credit no P2 arm. Rendering is local;
    publishing it is a separate, authorised step.
    """
    if max_minutes < min_minutes:
        typer.echo("Error: --max-minutes is below --min-minutes", err=True)
        raise typer.Exit(code=2)
    factory = platform_session_factory()
    with factory() as session:
        history = list_catalog_albums(session, brand_id)[:albums]
    candidate_ids: list[str] = []
    for album in history:
        rows = album.track_signatures if isinstance(album.track_signatures, list) else []
        for row in rows:
            cid = row.get("candidate_id") if isinstance(row, dict) else None
            if isinstance(cid, str) and cid not in candidate_ids:
                candidate_ids.append(cid)
    if not candidate_ids:
        typer.echo(f"Error: brand {brand_id} has no released tracks to compile", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"Offering {len(candidate_ids)} released tracks from {len(history)} albums")
    _run_assembly(AssemblePackageRequest(
        candidate_ids=candidate_ids,
        brand_id=brand_id,
        duration_range_seconds=DurationRangeSeconds(
            min_seconds=min_minutes * 60, max_seconds=max_minutes * 60),
        visual_profile=visual_profile,
        playlist_concept=playlist_concept,
        session_compilation=True,
    ))


@media_cli.command(name="inspect-album-textures")
def cli_inspect_album_textures(
    brand_id: str = typer.Option(
        ..., "--brand-id", help="Explicit brand ID; no global catalog search",
    ),
    limit: int = typer.Option(
        20, "--limit", min=1, max=100, help="Read 1..100 packages per page",
    ),
    after_id: str | None = typer.Option(
        None, "--after-id", help="Package ID cursor returned by preceding page",
    ),
) -> None:
    """Inspect persisted texture metadata only: no audio reads or writes.

    An evidence_structure_valid row is NOT proof of original source audio,
    recording distinctiveness, licensing or approval for publishing.
    """
    try:
        factory = platform_session_factory()
        with factory() as session:
            result = inspect_brand_album_textures(
                session, brand_id, page_size=limit, after_package_id=after_id,
            )
            session.rollback()
    except ValueError as exc:
        typer.echo(f"Invalid inventory selection: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    for item in result.entries:
        reason = f"; reason: {item.reason_code}" if item.reason_code else ""
        typer.echo(
            f"Package: {item.package_id}; release: {item.release_manifest_id}; "
            f"metadata: {item.classification}{reason}"
        )
    typer.echo(f"Rows: {len(result.entries)}; more: {result.has_more}")
    if result.next_after_id is not None:
        typer.echo(f"Next --after-id: {result.next_after_id}")
    typer.echo(
        "Metadata inventory only: original audio, checksums, rights and full "
        "catalog coverage are NOT verified."
    )


@media_cli.command(name="backfill-album-texture")
def cli_backfill_album_texture(
    package_id: str = typer.Option(
        ..., "--package-id", "-p", help="Exact pre-existing media package id",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Persist checked evidence; default is read-only dry run",
    ),
) -> None:
    """Verify stored original audio and optionally backfill one old album.

    Does not synthesize audio, upload, publish, change rights or edit the C6
    manifest. If any fact is unverifiable, no database update is committed.
    """
    platform = load_settings()
    store = MinioArtifactStore(platform)
    factory = platform_session_factory()
    with factory() as session:
        try:
            result = backfill_package_texture(
                session, store, package_id, apply=apply, now=SystemClock().now(),
            )
            if result.applied:
                session.commit()
            else:
                session.rollback()
        except (MediaAssemblyError, MediaInfrastructureError) as exc:
            session.rollback()
            typer.echo(f"Backfill refused: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    typer.echo(
        f"Package: {result.media_package_id}; verified tracks: {result.track_count}; "
        f"status: {result.status}; stored: {result.applied}"
    )


@media_cli.command(name="backfill-album-textures")
def cli_backfill_album_textures(
    package_ids: list[str] = typer.Option(  # noqa: B008 - Typer CLI declaration
        ..., "--package-id", "-p",
        help="Explicit package ID; repeat for each album (max 20)",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Commit each verified package; default is dry run",
    ),
) -> None:
    """Check up to 20 named archived albums, with separate transactions.

    A refusal never writes that package. Partial successes are reported;
    previously committed packages are not undone by a later refusal.
    """
    platform = load_settings()
    store = MinioArtifactStore(platform)
    factory = platform_session_factory()
    try:
        batch = backfill_packages_texture(
            factory, store, package_ids, apply=apply, now=SystemClock().now(),
        )
    except ValueError as exc:
        typer.echo(f"Invalid backfill selection: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    for item in batch.succeeded:
        typer.echo(
            f"Package: {item.media_package_id}; verified tracks: {item.track_count}; "
            f"status: {item.status}; stored: {item.applied}"
        )
    for package_id, reason in batch.refused:
        typer.echo(f"Package: {package_id}; refused: {reason}", err=True)
    if batch.refused:
        typer.echo(
            f"Batch incomplete: {len(batch.succeeded)} succeeded, "
            f"{len(batch.refused)} refused; successful --apply writes remain committed",
            err=True,
        )
        raise typer.Exit(code=2)


@media_cli.command(name="render-jobs")
def cli_render_jobs(
    package_id: str = typer.Option(
        ..., "--package-id", "-p", help="Exact media package ID to inspect",
    ),
    limit: int = typer.Option(
        20, "--limit", min=1, max=100, help="Show up to 100 recent render jobs",
    ),
) -> None:
    """Inspect render jobs without starting, retrying, or changing a render."""
    try:
        factory = platform_session_factory()
        with factory() as session:
            summaries = list_render_job_summaries(
                session, media_package_id=package_id, limit=limit,
            )
            session.rollback()
    except ValueError as exc:
        typer.echo(f"Invalid render history selection: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    for job in summaries:
        started = job.started_at.isoformat() if job.started_at else "unknown"
        finished = job.finished_at.isoformat() if job.finished_at else "unknown"
        typer.echo(
            f"Job: {job.job_id}; profile: {job.render_profile}; "
            f"attempt: {job.attempt_count}; status: {job.status.value}; "
            f"started: {started}; finished: {finished}; "
            f"reason: {job.error_code or 'none'}"
        )
    typer.echo(f"Rows: {len(summaries)}; maximum requested: {limit}")
    typer.echo(
        "Persisted records only: RUNNING does not prove worker liveness; "
        "absence of a job does not prove no render occurred. "
        "No automatic recovery, retry or artifact verification."
    )


@media_cli.command(name="render-inspect")
def cli_render_inspect(
    job_id: str = typer.Option(..., "--job-id", help="Exact persisted render job ID"),
    brand_id: str = typer.Option(
        ..., "--brand-id", help="Brand path hint (unverified without a package row)",
    ),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    record_inventory: bool = typer.Option(
        False, "--record-inventory",
        help="Persist a complete listing of a QUARANTINED claim as recovery evidence",
    ),
    recorded_by: str | None = typer.Option(
        None, "--recorded-by", help="Operator identity, required with --record-inventory",
    ),
) -> None:
    """Inventory one render claim's MinIO object names; optionally record it (ADR-0043)."""
    if record_inventory and not (recorded_by and recorded_by.strip()):
        typer.echo("--record-inventory requires --recorded-by", err=True)
        raise typer.Exit(code=2)
    recorded = False
    try:
        factory = platform_session_factory()
        store = MinioArtifactStore(load_settings())
        with factory() as session:
            inspection = inspect_render_claim(
                session, store, job_id=job_id, brand_id=brand_id, limit=limit,
            )
            if record_inventory:
                assert recorded_by is not None
                recorded = record_claim_inventory(
                    session, inspection, recorded_by=recorded_by.strip(),
                    recorded_at=SystemClock().now(),
                )
                session.commit()
            else:
                session.rollback()
    except (ValueError, MediaInfrastructureError) as exc:
        typer.echo(f"Render inspection refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"Job: {inspection.job_id}; status: {inspection.job_status.value}; "
        f"brand evidence: {inspection.brand_source}; reason: {inspection.reason}"
    )
    if inspection.claim_namespace is not None:
        typer.echo(f"Claim namespace: {inspection.claim_namespace}")
    if inspection.evidence is None:
        typer.echo("No artifact listing performed: execution identity is unverified.")
    else:
        for item in inspection.evidence.objects:
            typer.echo(
                f"Object: {item.uri}; pinned-key-hash: "
                f"{item.pinned_sha256 or 'invalid'}; "
                f"reported-bytes: {item.size_bytes if item.size_bytes is not None else 'unknown'}"
            )
        typer.echo(
            f"Observed objects: {len(inspection.evidence.objects)}; "
            f"truncated: {inspection.evidence.truncated}; limit: {limit}"
        )
    if record_inventory:
        typer.echo(
            "Recorded orphan_artifacts evidence." if recorded
            else "Identical orphan_artifacts evidence was already recorded."
        )
    typer.echo(
        "Object NAMES only: no bytes, integrity, worker termination, "
        "rights, recoverability or safe deletion verified. "
        + ("No retry or deletion." if record_inventory else "No retry or mutation.")
    )


@media_cli.command(name="render-attest-termination")
def cli_render_attest_termination(
    job_id: str = typer.Option(..., "--job-id", help="Exact RUNNING render job ID"),
    evidence_ref: str = typer.Option(
        ..., "--evidence-ref",
        help="Where the out-of-band proof lives (ticket, log, host check output)",
    ),
    recorded_by: str = typer.Option(..., "--recorded-by", help="Operator identity"),
) -> None:
    """Record that a claimed render's worker is verified stopped, then fence the claim.

    The operator must have verified termination out of band (for example the
    worker container is gone). Age alone is not proof. Nothing is retried.
    """
    try:
        factory = platform_session_factory()
        with factory() as session:
            job = get_render_job(session, job_id)
            if job is None or job.worker_id is None or job.started_at is None:
                raise ValueError("render job has no committed execution identity")
            # The attestation, not the operator's clock, proves termination;
            # never let a worker clock ahead of this host defeat the fence.
            now = max(SystemClock().now(), job.started_at)
            record_render_recovery_evidence(
                session, job_id=job.id, kind=RecoveryEvidenceKind.WORKER_TERMINATION,
                method="supervisor_attestation", worker_id=job.worker_id,
                started_at=job.started_at, input_manifest_hash=job.input_manifest_hash,
                recorded_by=recorded_by.strip(), recorded_at=now,
                evidence_ref=evidence_ref, detail={},
            )
            fenced = quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=job.input_manifest_hash,
                started_before=now, quarantined_at=now,
                expected_worker_id=job.worker_id, expected_started_at=job.started_at,
            )
            session.commit()
            current = get_render_job(session, job_id)
    except ValueError as exc:
        typer.echo(f"Termination attestation refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not fenced and (current is None or current.job_status is not RenderJobStatus.QUARANTINED):
        state = current.job_status.value if current is not None else "missing"
        typer.echo(
            f"Job: {job_id}; termination evidence recorded, but the claim was NOT "
            f"fenced (status {state}). No retry was staged.", err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(
        f"Job: {job_id}; termination evidence recorded; "
        f"claim {'fenced as QUARANTINED' if fenced else 'already fenced'}. "
        "No retry was staged and no artifact was touched."
    )


@media_cli.command(name="package")
def cli_package(
    package_id: str = typer.Option(..., "--package-id", "-p", help="Media package ID to inspect"),
) -> None:
    """Retrieve an assembled media package record."""
    factory = platform_session_factory()
    with factory() as session:
        pkg = get_media_package_by_id(session, package_id)
        if not pkg:
            typer.echo(f"No media package found for ID {package_id}.", err=True)
            raise typer.Exit(code=1)

        typer.echo(f"Package ID:       {pkg.media_package_id}")
        typer.echo(f"Release Manifest: {pkg.release_manifest_id}")
        typer.echo(f"Experiment ID:    {pkg.experiment_id}")
        typer.echo(f"Brand ID:         {pkg.brand_id}")
        typer.echo(f"Video Master URI: {pkg.video_master_uri}")
        typer.echo(f"Status:           {pkg.status.value}")


def _art_inbox() -> Path:
    inbox = load_media_factory_settings(platform=load_settings()).thumbnail_art_inbox
    if inbox is None:
        typer.echo(f"Error: {INBOX_VAR} is not set", err=True)
        raise typer.Exit(code=1)
    return inbox


@media_cli.command(name="thumbnail-job")
def cli_thumbnail_job(
    package_id: str = typer.Argument(..., help="Media package to write art prompts for"),
    visual_world: str = typer.Option(..., "--visual-world", help="The package's P3 visual world"),
    thumbnail_family: str | None = typer.Option(
        None, "--thumbnail-family", help="P2's thumbnail style for variant A"),
) -> None:
    """Write a package's thumbnail art prompts without changing its status
    (an already published video's new art; a waiting package gets its job at assembly)."""
    inbox = _art_inbox()
    with platform_session_factory()() as session:
        pkg = get_media_package_by_id(session, package_id)
    if pkg is None:
        typer.echo(f"No media package found for ID {package_id}.", err=True)
        raise typer.Exit(code=1)
    guide = load_style_guide()
    folder = write_job(inbox, package_id=package_id,
                       title=str(pkg.metadata_candidates[0]["title"]),
                       guide_version=guide.guide_version,
                       prompts=build_prompts(guide, package_id=package_id,
                                             thumbnail_family=thumbnail_family,
                                             visual_world=visual_world))
    typer.echo(f"Prompts: {folder / 'PROMPTS.md'}")


@media_cli.command(name="thumbnail-import")
def cli_thumbnail_import(
    package_id: str = typer.Argument(..., help="Package waiting at awaiting_thumbnail_art"),
) -> None:
    """Check the owner's three images, render the video as image A held still over the
    audio master and, if it passes QA, release the package."""
    inbox = _art_inbox()
    platform = load_settings()
    with platform_session_factory()() as session:
        try:
            pkg = import_owner_art(session, MinioArtifactStore(platform), FFmpegToolchain(),
                                   package_id, inbox / package_id, now=SystemClock().now())
        except MediaAssemblyError as exc:
            typer.echo(f"Not imported: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        session.commit()
    typer.echo(f"{package_id} is {pkg.status.value} with thumbnails "
               f"{', '.join(t['id'] for t in pkg.thumbnails)}")


def serve(host: str = "127.0.0.1", port: int = 8006) -> None:
    """Serve the `/media` FastAPI application using uvicorn."""
    platform = load_settings()
    settings = load_media_factory_settings(platform=platform, host=host, port=port)
    factory = platform_session_factory()

    from .api.app import create_app

    app = create_app(session_factory=factory, settings=settings, clock=SystemClock(),
                     backend=deployed_backend(platform, factory))
    uvicorn.run(app, host=host, port=port)

"""CLI entry points and commands for Playlist, Visual & Video Factory service (P6 §45)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import typer
import uvicorn
from plm_platform.clock import SystemClock
from plm_platform.config import load_settings
from plm_platform.db import session_factory as platform_session_factory

from .api.schemas import AssemblePackageRequest
from .artifacts import MinioArtifactStore
from .backend import deployed_backend
from .config import load_media_factory_settings
from .domain.enums import PackageStatus
from .errors import MediaAssemblyError, MediaInfrastructureError
from .render_reconciliation import inspect_render_claim
from .service import assemble_and_persist
from .storage.render_claims import list_render_job_summaries
from .storage.repositories import get_media_package_by_id
from .texture_backfill import backfill_package_texture, backfill_packages_texture
from .texture_inventory import inspect_brand_album_textures

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
    platform = load_settings()
    factory = platform_session_factory()
    backend = deployed_backend(platform, factory)
    if backend is None:
        typer.echo("blocked: no service token (PLM_SERVICE_TOKEN) to ask P5", err=True)
        raise typer.Exit(code=3)
    with factory() as session:
        try:
            pkg = assemble_and_persist(
                session, backend, req, clock=SystemClock(),
                default_render_profile=load_media_factory_settings(
                    platform=platform).default_render_profile,
                render_session_factory=factory,
                worker_id_factory=lambda: uuid4().hex)
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
    if pkg.status is not PackageStatus.READY_FOR_PUBLISH:
        codes = (pkg.render_report.get("qa") or {}).get("reason_codes") or []
        typer.echo(f"Blocked by post-render QA: {', '.join(codes)}", err=True)
        raise typer.Exit(code=2)



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
) -> None:
    """Read-only inventory of one render claim's MinIO object names, not recovery."""
    try:
        factory = platform_session_factory()
        store = MinioArtifactStore(load_settings())
        with factory() as session:
            inspection = inspect_render_claim(
                session, store, job_id=job_id, brand_id=brand_id, limit=limit,
            )
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
    typer.echo(
        "Read-only object NAMES only: no bytes, integrity, worker termination, "
        "rights, recoverability or safe deletion verified. No retry or mutation."
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


def serve(host: str = "127.0.0.1", port: int = 8006) -> None:
    """Serve the `/media` FastAPI application using uvicorn."""
    platform = load_settings()
    settings = load_media_factory_settings(platform=platform, host=host, port=port)
    factory = platform_session_factory()

    from .api.app import create_app

    app = create_app(session_factory=factory, settings=settings, clock=SystemClock(),
                     backend=deployed_backend(platform, factory))
    uvicorn.run(app, host=host, port=port)

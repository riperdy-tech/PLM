"""CLI entry points and commands for Playlist, Visual & Video Factory service (P6 §45)."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from plm_platform.clock import SystemClock
from plm_platform.config import load_settings
from plm_platform.db import session_factory as platform_session_factory

from .api.schemas import AssemblePackageRequest
from .backend import deployed_backend
from .config import load_media_factory_settings
from .domain.enums import PackageStatus
from .errors import MediaAssemblyError, MediaInfrastructureError
from .service import assemble_and_persist
from .storage.repositories import get_media_package_by_id

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
            pkg = assemble_and_persist(session, backend, req, clock=SystemClock())
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

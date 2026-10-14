"""CLI entry points and commands for Playlist, Visual & Video Factory service (P6 §45)."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.config import load_settings
from plm_platform.db import session_factory as platform_session_factory

from .api.schemas import AssemblePackageRequest
from .config import load_media_factory_settings
from .rendering.assembler import assemble_media_package
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
from .visuals.planner import BrandProfile

media_cli = typer.Typer(help="Media factory CLI commands")


@media_cli.command(name="assemble")
def cli_assemble(
    request_path: str = typer.Option(
        ...,
        "--request",
        "-r",
        help="Path to an assembly request JSON: track_manifests (C5), brand_id, brand_name, "
        "required_duration_seconds, visual_profile, playlist_concept",
    ),
) -> None:
    """Assemble and render a media package from P5 track manifests (ADR-0028)."""
    path = Path(request_path)
    if not path.is_file():
        typer.echo(f"Error: request file {request_path} does not exist.", err=True)
        raise typer.Exit(code=1)
    try:
        req = AssemblePackageRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))
        manifests = [parse_seam(ReleaseManifestPayload, m) for m in req.track_manifests]
        result = assemble_media_package(
            track_manifests=manifests,
            brand_profile=BrandProfile(brand_id=req.brand_id, brand_name=req.brand_name),
            required_duration_seconds=req.required_duration_seconds,
            visual_profile=req.visual_profile,
            playlist_concept=req.playlist_concept,
            render_profile=req.render_profile,
            sequence_version=req.sequence_version,
        )
    except (ValueError, SeamRejection) as exc:
        typer.echo(f"Error: assembly refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    pkg = result.media_package
    typer.echo(f"Media Package ID:    {pkg.media_package_id}")
    typer.echo(f"Release Manifest ID: {pkg.release_manifest_id}")
    typer.echo(f"Video Master:        {pkg.video_master_uri}")
    typer.echo(f"Video Checksum:      {pkg.video_checksum}")
    typer.echo(f"Duration:            {pkg.video_duration_seconds:.2f}s")
    typer.echo(f"Thumbnails:          {len(pkg.thumbnails)} variants")
    typer.echo(f"Chapters:            {len(pkg.chapters)}")
    typer.echo(f"Status:              {pkg.status.value}")

    # Persist to database
    factory = platform_session_factory()
    with factory() as session:
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
        session.commit()
    typer.echo("Media package successfully persisted to database.")


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
    settings = load_media_factory_settings(platform=load_settings(), host=host, port=port)
    factory = platform_session_factory()

    from .api.app import create_app

    app = create_app(session_factory=factory, settings=settings)
    uvicorn.run(app, host=host, port=port)

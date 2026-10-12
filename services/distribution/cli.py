"""CLI commands implementation for Phase P8 DSP Distribution Engine."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from plm_platform.db import build_engine, session_factory

from .api.app import create_app
from .artists.manager import get_or_create_artist
from .client.mock import MockDistributorClient
from .compiler.release_compiler import compile_canonical_release_package
from .engine import DistributionEngine
from .reports.ingest import get_report_reference
from .storage.repositories import get_release_by_id


def cli_submit(
    package_path: str,
    artist_name: str = "Neon Artist",
    brand_id: str = "brand_neon",
) -> None:
    """Submit a release package JSON file for distribution."""
    path = Path(package_path)
    if not path.is_file():
        typer.echo(f"Error: package file {package_path} not found", err=True)
        raise typer.Exit(code=1)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Error reading package JSON: {exc}", err=True)
        raise typer.Exit(code=1)

    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        artist = get_or_create_artist(session, artist_name, brand_id=brand_id)

        p5_manifest = {
            "p5_manifest_id": data.get("p5_manifest_id", "p5_cli_def"),
            "disposition": "PASSED",
        }
        media_package = {
            "media_package_id": data.get("media_package_id", "p6_cli_def"),
            "brand_id": brand_id,
            "tracklist": data.get("tracklist", []),
            "thumbnails": data.get("thumbnails", [{"uri": "media/artwork/cli.jpg"}]),
            "audio_master_uri": data.get("audio_master_uri", "media/audio/cli.wav"),
        }

        package = compile_canonical_release_package(
            p5_manifest=p5_manifest,
            media_package=media_package,
            artist_id=artist.id,
            artist_name=artist.canonical_name,
            brand_id=brand_id,
            release_title=data.get("title", "CLI Release"),
        )

        dist_engine = DistributionEngine(MockDistributorClient())
        result = dist_engine.submit_release(session, package, artist=artist)
        session.commit()

        typer.echo(f"Submitted release: {result.release.release_id} (status={result.release.status.value})")


def cli_release(release_id: str) -> None:
    """Show details of a distribution release."""
    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        release = get_release_by_id(session, release_id)
        if not release:
            typer.echo(f"Release not found: {release_id}", err=True)
            raise typer.Exit(code=1)

        typer.echo(f"Release ID: {release.release_id}")
        typer.echo(f"Artist ID: {release.artist_id}")
        typer.echo(f"Title: {release.release_title}")
        typer.echo(f"Status: {release.status.value}")
        typer.echo(f"Provider: {release.provider} (ID: {release.provider_release_id})")
        typer.echo(f"Deliveries: {len(release.deliveries)} target stores")


def cli_report(report_id: str) -> None:
    """Show details of an ingested raw distributor report (Ruling R10)."""
    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        report = get_report_reference(session, report_id)
        if not report:
            typer.echo(f"Report not found: {report_id}", err=True)
            raise typer.Exit(code=1)

        typer.echo(f"Report ID: {report.report_id}")
        typer.echo(f"Provider: {report.provider}")
        typer.echo(f"Period: {report.provider_period}")
        typer.echo(f"Checksum: {report.report_checksum}")
        typer.echo(f"URI: {report.raw_payload_uri}")


def serve(host: str = "127.0.0.1", port: int = 8008) -> None:
    """Serve the `/distribution` FastAPI application."""
    app = create_app()
    uvicorn.run(app, host=host, port=port)

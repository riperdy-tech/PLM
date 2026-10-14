"""CLI commands implementation for Phase P8 DSP Distribution Engine."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from plm_platform.clock import SystemClock
from plm_platform.db import build_engine, session_factory

from .api.app import create_app
from .api.schemas import ReleaseSubmitRequest
from .client.mock import MockDistributorClient
from .engine import DistributionEngine
from .reports.ingest import get_report_reference
from .storage.repositories import get_release_by_id
from .submission import ReleaseMetadata, SubmissionRefusedError, submit_release_from_seams


def cli_submit(package_path: str) -> None:
    """Submit a release from a JSON file holding the ReleaseSubmitRequest fields."""
    path = Path(package_path)
    if not path.is_file():
        typer.echo(f"Error: package file {package_path} not found", err=True)
        raise typer.Exit(code=1)
    try:
        req = ReleaseSubmitRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        typer.echo(f"Error: request refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    clock = SystemClock()
    engine = build_engine()
    factory = session_factory(engine)
    with factory() as session:
        try:
            result = submit_release_from_seams(
                session,
                DistributionEngine(MockDistributorClient(clock=clock)),
                track_manifests=req.track_manifests,
                media_package=req.media_package,
                publication=req.publication,
                metadata=ReleaseMetadata(
                    artist_name=req.artist_name,
                    release_date=req.release_date,
                    label_name=req.label_name,
                    copyright_line=req.copyright_line,
                    publisher_line=req.publisher_line,
                    territories=req.territories,
                    release_title=req.release_title,
                    release_version=req.release_version,
                ),
                now=clock.now(),
            )
        except SubmissionRefusedError as exc:
            typer.echo(f"Error: release refused: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        session.commit()
        typer.echo(
            f"Submitted release: {result.release.release_id} (status={result.release.status.value})"
        )


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

"""CLI implementation for Phase P7 Autonomous YouTube Publishing Engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
import uvicorn
from plm_contracts import ProviderMode
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c6 import MediaPackagePayload
from plm_platform.clearance import clearance_verifier_for
from plm_platform.clock import Clock, SystemClock
from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from plm_platform.execution import load_execution_settings

from .client.mock import MockYouTubeClient
from .client.real import RealYouTubeClient
from .domain.enums import OperatingMode
from .engine import YouTubePublishingEngine
from .manifests import (
    CandidateSelectionError,
    PackageNotReadyError,
    build_publish_manifest,
    preflight_metadata,
    same_publish_choice,
)
from .preflight.gates import evaluate_preflight_all
from .storage.repositories import (
    get_channel_profile_by_channel_id,
    get_publish_attempt_by_id,
    get_publish_manifest_by_id,
    insert_publish_manifest,
)

CLOCK: Clock = SystemClock()
"""The CLI's time source; the only wall-clock read in the P7 command line."""


def cli_publish(
    package_path: str,
    channel_id: str,
    mode: str = "dry_run",
    metadata_variant_id: str | None = None,
    thumbnail_id: str | None = None,
) -> None:
    """Publish a Seam C6 hop-1 media package JSON file to a configured channel."""
    path = Path(package_path)
    if not path.is_file():
        typer.echo(f"Error: package file {package_path} not found", err=True)
        raise typer.Exit(code=1)
    try:
        pkg = parse_seam(MediaPackagePayload, json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, SeamRejection) as exc:
        typer.echo(f"Error: media package refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    now = CLOCK.now()
    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        profile = get_channel_profile_by_channel_id(session, channel_id)
        if not profile:
            typer.echo(
                f"Error: no channel profile for {channel_id!r}; an operator must configure it",
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            manifest = build_publish_manifest(
                pkg, profile, publish_at=None, now=now,
                metadata_variant_id=metadata_variant_id, thumbnail_id=thumbnail_id,
            )
        except (PackageNotReadyError, CandidateSelectionError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        passed, results, reason = evaluate_preflight_all(
            profile, pkg.to_wire(),
            preflight_metadata(pkg, metadata_variant_id=manifest.metadata_version),
            now=now, selected_thumbnail_id=manifest.selection.thumbnail_id if manifest.selection else None,
        )
        if not passed:
            diags = [d for r in results for d in r.diagnostics]
            typer.echo(f"Preflight check failed ({reason}): {'; '.join(diags)}", err=True)
            raise typer.Exit(code=1)

        stored = get_publish_manifest_by_id(session, manifest.manifest_id)
        if stored is not None:
            if not same_publish_choice(
                stored, manifest,
                explicit_requested=metadata_variant_id is not None or thumbnail_id is not None,
            ):
                typer.echo("candidate_selection_conflict: frozen publication differs", err=True)
                raise typer.Exit(code=2)
            manifest = stored
        else:
            insert_publish_manifest(session, manifest)
            session.commit()

        exec_settings = load_execution_settings()
        client = (
            RealYouTubeClient()
            if exec_settings.provider_mode("youtube") == ProviderMode.REAL
            else MockYouTubeClient()
        )
        yt_engine = YouTubePublishingEngine(
            client,
            clock=CLOCK,
            clearance=clearance_verifier_for(load_settings(), factory),
        )
        result = yt_engine.execute_publish(session, manifest, operating_mode=OperatingMode(mode))

        out: dict[str, Any] = {
            "attempt_id": result.attempt.attempt_id,
            "manifest_id": manifest.manifest_id,
            "candidate_selection": manifest.selection.model_dump(mode="json") if manifest.selection else None,
            "state": result.attempt.state.value,
            "youtube_video_id": result.attempt.youtube_video_id,
            "error": result.error,
        }
        if result.publication_record:
            out["video_url"] = result.publication_record.video_url
        typer.echo(json.dumps(out, indent=2))


def cli_attempt(attempt_id: str) -> None:
    """Retrieve the status of a publishing attempt."""
    engine = build_engine()
    factory = session_factory(engine)
    with factory() as session:
        att = get_publish_attempt_by_id(session, attempt_id)
        if not att:
            typer.echo(f"Attempt {attempt_id} not found", err=True)
            raise typer.Exit(code=1)
        typer.echo(
            json.dumps(
                {
                    "attempt_id": att.attempt_id,
                    "manifest_id": att.manifest_id,
                    "channel_id": att.channel_id,
                    "state": att.state.value,
                    "youtube_video_id": att.youtube_video_id,
                    "bytes_uploaded": att.bytes_uploaded,
                    "total_bytes": att.total_bytes,
                    "error_code": att.error_code,
                    "error_message": att.error_message,
                },
                indent=2,
            )
        )


def cli_reconcile(attempt_id: str) -> None:
    """Reconcile a publishing attempt with remote YouTube state."""
    engine = build_engine()
    factory = session_factory(engine)
    with factory() as session:
        att = get_publish_attempt_by_id(session, attempt_id)
        if not att:
            typer.echo(f"Attempt {attempt_id} not found", err=True)
            raise typer.Exit(code=1)
        if not att.youtube_video_id:
            typer.echo(f"Attempt {attempt_id} has no remote video ID", err=True)
            raise typer.Exit(code=1)
        client = MockYouTubeClient()
        remote = client.get_video(att.youtube_video_id) or {}
        typer.echo(
            json.dumps(
                {
                    "attempt_id": att.attempt_id,
                    "state": att.state.value,
                    "youtube_video_id": att.youtube_video_id,
                    "remote_status": remote.get("status", {}),
                },
                indent=2,
            )
        )


def serve(host: str = "127.0.0.1", port: int = 8007) -> None:
    """Serve the `/youtube` API service via uvicorn."""
    from .api.app import create_app

    exec_settings = load_execution_settings()
    client = (
        RealYouTubeClient()
        if exec_settings.provider_mode("youtube") == ProviderMode.REAL
        else MockYouTubeClient()
    )
    app = create_app(client=client)
    uvicorn.run(app, host=host, port=port)

"""CLI implementation for Phase P7 Autonomous YouTube Publishing Engine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import uvicorn
from plm_platform.db import build_engine, session_factory

from .client.mock import MockYouTubeClient
from .domain.enums import OperatingMode, PrivacyStatus
from .domain.ids import build_idempotency_key, build_manifest_id
from .domain.models import ChannelProfile, PublishManifest
from .engine import YouTubePublishingEngine
from .preflight.gates import evaluate_preflight_all
from .storage.repositories import (
    get_channel_profile_by_channel_id,
    get_publish_attempt_by_id,
    insert_channel_profile,
    insert_publish_manifest,
)


def cli_publish(
    package_path: str,
    channel_id: str = "UC_neon_records",
    mode: str = "dry_run",
) -> None:
    """Publish a media package JSON file to YouTube."""
    path = Path(package_path)
    if not path.is_file():
        typer.echo(f"Error: package file {package_path} not found", err=True)
        raise typer.Exit(code=1)

    try:
        pkg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Error reading package JSON: {exc}", err=True)
        raise typer.Exit(code=1)

    now = datetime.now(UTC)
    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        profile = get_channel_profile_by_channel_id(session, channel_id)
        if not profile:
            profile = ChannelProfile(
                id=f"prof_{channel_id}",
                channel_id=channel_id,
                channel_name="Neon Records Channel",
                brand_id=pkg.get("brand_id", "brand_neon"),
                oauth_credential_ref="sec_oauth_neon",
                default_privacy_mode=PrivacyStatus.PRIVATE,
                operating_mode=OperatingMode(mode),
                created_at=now,
                updated_at=now,
            )
            insert_channel_profile(session, profile)
            session.commit()

        metadata_cands = pkg.get("metadata_candidates", [{}])
        meta = metadata_cands[0] if metadata_cands else {}
        title = meta.get("title", f"Release {pkg.get('media_package_id')}")
        description = meta.get("description", "Synthetic music release.")
        tags = meta.get("tags", ["music", "synth"])

        passed, results, reason = evaluate_preflight_all(
            profile,
            pkg,
            {"title": title, "description": description, "tags": tags, "category_id": "10"},
        )
        if not passed:
            diags = [d for r in results for d in r.diagnostics]
            typer.echo(f"Preflight check failed ({reason}): {'; '.join(diags)}", err=True)
            raise typer.Exit(code=1)

        video_info = pkg.get("video", {})
        video_chk = video_info.get("checksum", "chk_def")
        media_pkg_id = pkg.get("media_package_id", "pkg_default")
        manifest_id = build_manifest_id(channel_id, media_pkg_id, video_chk)
        idem_key = build_idempotency_key(channel_id, media_pkg_id, video_chk)

        manifest = PublishManifest(
            manifest_id=manifest_id,
            channel_id=channel_id,
            media_package_id=media_pkg_id,
            release_manifest_id=pkg.get("release_manifest_id", "rm_default"),
            experiment_id=pkg.get("experiment_id", "exp_default"),
            experiment_family_id=pkg.get("experiment_family_id", "fam_default"),
            content_arm_id=pkg.get("content_arm_id", "arm_default"),
            content_genome_id=pkg.get("content_genome_id", "gen_default"),
            production_directive_id=pkg.get("production_directive_id", "dir_default"),
            brand_id=pkg.get("brand_id", "brand_neon"),
            idempotency_key=idem_key,
            video_master_uri=video_info.get("master_uri", "media/video/default.mp4"),
            video_checksum=video_chk,
            video_duration_seconds=float(video_info.get("duration_seconds", 300.0)),
            thumbnail_uri=pkg.get("thumbnails", [{}])[0].get("uri", "media/thumb/default.jpg"),
            thumbnail_checksum=pkg.get("thumbnails", [{}])[0].get("id", "chk_thumb"),
            title=title,
            description=description,
            tags=tags,
            category_id="10",
            privacy_status=PrivacyStatus.PRIVATE,
            contains_synthetic_media=True,
            made_for_kids=False,
            playlist_id="PL_neon_official",
            metadata_version="v1",
            created_at=now,
        )
        insert_publish_manifest(session, manifest)
        session.commit()

        yt_engine = YouTubePublishingEngine(MockYouTubeClient())
        result = yt_engine.execute_publish(session, manifest, operating_mode=OperatingMode(mode))

        out: dict[str, Any] = {
            "attempt_id": result.attempt.attempt_id,
            "manifest_id": manifest.manifest_id,
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

    app = create_app()
    uvicorn.run(app, host=host, port=port)

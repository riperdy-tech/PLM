"""Controlled production dry-run smoke test for PHASE-P6 (P6 §45, Ruling R9).

Runs the full P6 media factory pipeline hermetically:
C5 release manifest ingestion → playlist sequencing → visual session planning
→ artwork/thumbnail candidate generation → metadata compilation
→ deterministic media rendering & QA → media package assembly (Seam C6).
"""

from __future__ import annotations

from datetime import UTC, datetime

from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..domain.enums import PackageStatus
from ..rendering.assembler import assemble_media_package
from ..storage.repositories import (
    get_media_package_by_id,
    insert_media_package,
    insert_playlist_session,
    insert_playlist_track,
    insert_render_job,
    insert_thumbnail_variant,
    insert_visual_asset,
    insert_visual_session,
)
from ..visuals.planner import BrandProfile

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending media factory service token (PLM_SERVICE_TOKEN)"


def run_smoke_test() -> str | None:
    """Run the P6 dry-run smoke test. Returns None on success, or blocked reason string."""
    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON

    # Test database connectivity
    engine = build_engine(platform)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return f"blocked: database unreachable ({exc})"

    now = datetime.now(UTC)
    ts = int(now.timestamp())

    release_manifest = {
        "release_manifest_id": f"rm_smoke_{ts}",
        "experiment_id": f"exp_smoke_{ts}",
        "content_arm_id": f"arm_smoke_{ts}",
        "brand_id": "brand_neon",
        "tracks": [
            {
                "song_id": f"song_smoke_{ts}_01",
                "title": "Neon Skyline",
                "duration_seconds": 120.0,
                "provenance_status": "PASSED",
                "audio_uri": f"media/audio/song_smoke_{ts}_01.wav",
                "audio_checksum": f"chk_smoke_{ts}_01",
            },
            {
                "song_id": f"song_smoke_{ts}_02",
                "title": "Cyber Rain",
                "duration_seconds": 150.0,
                "provenance_status": "PASSED",
                "audio_uri": f"media/audio/song_smoke_{ts}_02.wav",
                "audio_checksum": f"chk_smoke_{ts}_02",
            },
        ],
        "playlist_intent": "Midnight Study Session",
        "policy_flags": {"ai_disclosure_required": True},
    }

    brand = BrandProfile(
        brand_id="brand_neon",
        brand_name="Neon Records",
    )

    try:
        res = assemble_media_package(
            release_manifest=release_manifest,
            brand_profile=brand,
            render_profile="yt_longform_1080p_v1",
            required_duration_seconds=270.0,
        )

        if res.media_package.status != PackageStatus.READY_FOR_PUBLISH:
            return f"smoke test failed: package status {res.media_package.status.value}"

        if not res.media_package.video_master_uri:
            return "smoke test failed: missing video master URI"

        # Persist and query back
        factory = session_factory(engine)
        with factory() as session:
            insert_playlist_session(session, res.playlist_session)
            for tr in res.playlist_tracks:
                insert_playlist_track(session, tr)

            insert_visual_session(session, res.visual_session)
            for va in res.visual_assets:
                insert_visual_asset(session, va)

            for tv in res.thumbnail_variants:
                insert_thumbnail_variant(session, tv)

            insert_render_job(session, res.render_job)
            insert_media_package(session, res.media_package)
            session.commit()

            fetched = get_media_package_by_id(session, res.media_package.media_package_id)
            if not fetched:
                return "smoke test failed: persisted media package could not be retrieved"
            if fetched.media_package_id != res.media_package.media_package_id:
                return "smoke test failed: package ID mismatch"

    except Exception as exc:  # noqa: BLE001
        return f"smoke test raised exception: {exc}"

    return None

"""Controlled dry-run smoke test for Phase P7 Autonomous YouTube Publishing Engine (P7 §26)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..client.mock import MockYouTubeClient
from ..domain.enums import OperatingMode, PrivacyStatus
from ..domain.ids import build_idempotency_key, build_manifest_id
from ..domain.models import ChannelProfile, PublishManifest
from ..engine import YouTubePublishingEngine
from ..preflight.gates import evaluate_preflight_all
from ..storage.repositories import (
    get_channel_profile_by_channel_id,
    insert_channel_profile,
    insert_publish_manifest,
)

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending youtube publisher service token (PLM_SERVICE_TOKEN)"


def run_smoke_test() -> str | None:
    """Run the P7 dry-run smoke test. Returns None on success, or blocked reason string."""
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
    channel_id = f"UC_smoke_{ts}"

    package_payload: dict[str, Any] = {
        "media_package_id": f"pkg_smoke_{ts}",
        "release_manifest_id": f"rm_smoke_{ts}",
        "experiment_id": f"exp_smoke_{ts}",
        "experiment_family_id": f"fam_smoke_{ts}",
        "content_arm_id": f"arm_smoke_{ts}",
        "content_genome_id": f"gen_smoke_{ts}",
        "production_directive_id": f"dir_smoke_{ts}",
        "brand_id": "brand_neon",
        "video": {
            "master_uri": f"media/video/smoke_{ts}.mp4",
            "checksum": f"chk_video_{ts}",
            "duration_seconds": 180.0,
            "render_profile": "yt_longform_1080p_v1",
        },
        "thumbnails": [
            {
                "id": f"thumb_{ts}_01",
                "uri": f"media/thumbnails/smoke_{ts}_01.jpg",
            }
        ],
        "metadata_candidates": [
            {
                "title": "Neon Dreams | Lofi Hip Hop Study Beats",
                "description": "Relaxing late night lofi beats for study and focus.",
                "tags": ["lofi", "chill", "study"],
            }
        ],
    }

    try:
        factory = session_factory(engine)
        with factory() as session:
            # 1. Ensure channel profile
            profile = get_channel_profile_by_channel_id(session, channel_id)
            if not profile:
                profile = ChannelProfile(
                    id=f"prof_smoke_{ts}",
                    channel_id=channel_id,
                    channel_name="Neon Smoke Channel",
                    brand_id="brand_neon",
                    oauth_credential_ref="sec_oauth_smoke",
                    default_privacy_mode=PrivacyStatus.PRIVATE,
                    operating_mode=OperatingMode.DRY_RUN,
                    created_at=now,
                    updated_at=now,
                )
                insert_channel_profile(session, profile)
                session.commit()

            # 2. Preflight
            meta = {
                "title": "Neon Dreams | Lofi Hip Hop Study Beats",
                "description": "Relaxing late night lofi beats for study and focus.",
                "tags": ["lofi", "chill", "study"],
                "category_id": "10",
            }
            passed, _results, reason = evaluate_preflight_all(profile, package_payload, meta)
            if not passed:
                return f"preflight failed: {reason}"

            # 3. Create manifest
            idem_key = build_idempotency_key(channel_id, package_payload["media_package_id"], f"chk_video_{ts}")
            manifest_id = build_manifest_id(channel_id, package_payload["media_package_id"], f"chk_video_{ts}")

            manifest = PublishManifest(
                manifest_id=manifest_id,
                channel_id=channel_id,
                media_package_id=package_payload["media_package_id"],
                release_manifest_id=package_payload["release_manifest_id"],
                experiment_id=package_payload["experiment_id"],
                experiment_family_id=package_payload["experiment_family_id"],
                content_arm_id=package_payload["content_arm_id"],
                content_genome_id=package_payload["content_genome_id"],
                production_directive_id=package_payload["production_directive_id"],
                brand_id="brand_neon",
                idempotency_key=idem_key,
                video_master_uri=package_payload["video"]["master_uri"],
                video_checksum=package_payload["video"]["checksum"],
                video_duration_seconds=package_payload["video"]["duration_seconds"],
                thumbnail_uri=package_payload["thumbnails"][0]["uri"],
                thumbnail_checksum=package_payload["thumbnails"][0]["id"],
                title=str(meta["title"]),
                description=str(meta["description"]),
                tags=[str(t) for t in meta["tags"]],
                category_id="10",
                privacy_status=PrivacyStatus.PRIVATE,
                contains_synthetic_media=True,
                made_for_kids=False,
                playlist_id="PL_smoke_official",
                metadata_version="v1",
                created_at=now,
            )
            insert_publish_manifest(session, manifest)
            session.commit()

            # 4. Engine publish with mock client
            client = MockYouTubeClient()
            engine_runner = YouTubePublishingEngine(client)
            res = engine_runner.execute_publish(session, manifest)

            if not res.is_success:
                return f"publishing failed: state={res.attempt.state.value}, error={res.error}"

            if not res.attempt.youtube_video_id:
                return "publishing failed: missing youtube_video_id"

            if not res.event:
                return "publishing failed: missing Hop 2 event"

    except Exception as exc:  # noqa: BLE001
        return f"smoke test raised exception: {exc}"

    return None

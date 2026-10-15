"""Controlled dry-run smoke test for Phase P7 Autonomous YouTube Publishing Engine (P7 §26)."""

from __future__ import annotations

import hashlib
from typing import Any

from plm_contracts import CommercialUseEligibility, GateDisposition, ProvenanceStatus
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clearance import QueueHoldSource, verify_release_clearance
from plm_platform.clock import Clock, SystemClock
from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..client.mock import MockYouTubeClient
from ..domain.enums import OperatingMode, PrivacyStatus
from ..domain.ids import build_idempotency_key, build_manifest_id
from ..domain.models import ChannelProfile, ManifestTrack, PublishManifest
from ..engine import YouTubePublishingEngine
from ..preflight.gates import evaluate_preflight_all
from ..storage.repositories import (
    get_channel_profile_by_channel_id,
    insert_channel_profile,
    insert_publish_manifest,
)

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending youtube publisher service token (PLM_SERVICE_TOKEN)"


def run_smoke_test(*, clock: Clock | None = None) -> str | None:
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

    smoke_clock = SystemClock() if clock is None else clock
    now = smoke_clock.now()
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
            passed, _results, reason = evaluate_preflight_all(profile, package_payload, meta, now=now)
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
                tracks=(ManifestTrack(candidate_id=f"cand_smoke_{ts}",
                                      artifact_id=f"art_smoke_{ts}"),),
                created_at=now,
            )
            insert_publish_manifest(session, manifest)
            session.commit()

            # 4. Engine publish with mock client
            client = MockYouTubeClient()
            # P5 is not part of this smoke: the only recording it can clear is the one this
            # smoke declares. Holds are still read from the real exception queue.
            own = _smoke_release_manifest(package_payload, ts)
            engine_runner = YouTubePublishingEngine(
                client, clock=smoke_clock,
                clearance=lambda tracks: verify_release_clearance(
                    tracks, manifests=_OwnManifest(own), holds=QueueHoldSource(factory)
                ),
            )
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


class _OwnManifest:
    def __init__(self, manifest: ReleaseManifestPayload) -> None:
        self._manifest = manifest

    def release_manifest(self, candidate_id: str) -> ReleaseManifestPayload | None:
        return self._manifest if candidate_id == self._manifest.candidate_id else None


def _smoke_release_manifest(payload: dict[str, Any], ts: int) -> ReleaseManifestPayload:
    candidate = f"cand_smoke_{ts}"
    return ReleaseManifestPayload.build(
        p5_manifest_id=f"rel_man_smoke_{ts}", release_manifest_version="1.0",
        candidate_id=candidate, canonical_artifact_id=f"art_smoke_{ts}",
        artifact_uri=f"music/smoke/{candidate}.wav",
        artifact_sha256=hashlib.sha256(f"smoke_{ts}".encode()).hexdigest(),
        measured_duration_seconds=180.0, track_spec_id=f"trk_smoke_{ts}",
        lineage=ExperimentLineage(
            experiment_id=payload["experiment_id"],
            experiment_family_id=payload["experiment_family_id"],
            content_arm_id=payload["content_arm_id"],
            content_genome_id=payload["content_genome_id"],
            production_directive_id=payload["production_directive_id"],
        ),
        disposition=GateDisposition.PASSED, override=None, technical_status="PASSED",
        commercial_use_status=CommercialUseEligibility.ELIGIBLE,
        provenance_status=ProvenanceStatus.COMPLETE, ai_disclosure_required=True,
        rights_risk="LOW", policy_status="COMPLIANT", evaluation_id=f"eval_smoke_{ts}",
        evaluation_policy_version="p5-v1", warning_codes=(),
        evidence_bundle_id=f"evi_smoke_{ts}",
    )

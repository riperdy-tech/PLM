"""FastAPI routes for `/youtube` publishing service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from plm_contracts import ErrorShape

from ..client.mock import MockYouTubeClient
from ..domain.enums import OperatingMode, PrivacyStatus
from ..domain.ids import build_idempotency_key, build_manifest_id
from ..domain.models import ChannelProfile, PublishManifest
from ..engine import YouTubePublishingEngine
from ..preflight.gates import evaluate_preflight_all
from ..storage.repositories import (
    get_channel_profile_by_channel_id,
    get_publish_attempt_by_id,
    insert_channel_profile,
    insert_publish_manifest,
)
from .schemas import (
    AttemptStatusResponse,
    HealthResponse,
    PublishRequest,
    PublishResponse,
    ReconcileResponse,
)


def create_youtube_router(
    session_factory: Any,
    auth_dependency: Any,
    client: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/youtube", tags=["youtube"])
    yt_client = client or MockYouTubeClient()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            version="v1",
            timestamp=datetime.now(UTC),
        )

    @router.post("/publish", response_model=PublishResponse, dependencies=[Depends(auth_dependency)])
    def publish_package(req: PublishRequest) -> PublishResponse:
        pkg = req.media_package
        now = datetime.now(UTC)

        metadata_cands = pkg.get("metadata_candidates", [{}])
        primary_meta = metadata_cands[0] if metadata_cands else {}

        title = primary_meta.get("title", f"Release {pkg.get('media_package_id', 'unknown')}")
        description = primary_meta.get("description", "Synthetic music playlist.")
        tags = primary_meta.get("tags", ["lofi", "ambient", "synth"])

        video_info = pkg.get("video", {})
        video_uri = video_info.get("master_uri", "media/video/default.mp4")
        video_chk = video_info.get("checksum", "chk_video_default")
        duration = float(video_info.get("duration_seconds", 300.0))

        thumbs = pkg.get("thumbnails", [{}])
        first_thumb = thumbs[0] if thumbs else {}
        thumb_uri = first_thumb.get("uri", "media/thumbnails/default.jpg")
        thumb_chk = first_thumb.get("id", "chk_thumb_default")

        # Session block
        with session_factory() as session:
            # 1. Ensure channel profile exists
            profile = get_channel_profile_by_channel_id(session, req.channel_id)
            if not profile:
                profile = ChannelProfile(
                    id=f"prof_{req.channel_id}",
                    channel_id=req.channel_id,
                    channel_name="Neon Records Channel",
                    brand_id=pkg.get("brand_id", "brand_neon"),
                    oauth_credential_ref="sec_oauth_neon",
                    default_privacy_mode=PrivacyStatus.PRIVATE,
                    operating_mode=OperatingMode(req.operating_mode),
                    created_at=now,
                    updated_at=now,
                )
                insert_channel_profile(session, profile)
                session.commit()

            # 2. Preflight evaluation
            meta_dict = {
                "title": title,
                "description": description,
                "tags": tags,
                "category_id": "10",
            }
            passed, results, reason = evaluate_preflight_all(
                profile, pkg, meta_dict, req.publish_at
            )
            if not passed:
                diags = [d for r in results for d in r.diagnostics]
                raise HTTPException(
                    status_code=422,
                    detail=ErrorShape(
                        code=reason.value if reason else "preflight_failed",
                        message="; ".join(diags),
                    ).model_dump(),
                )

            # 3. Create PublishManifest
            media_pkg_id = pkg.get("media_package_id", f"pkg_{int(now.timestamp())}")
            idem_key = build_idempotency_key(req.channel_id, media_pkg_id, video_chk)
            manifest_id = build_manifest_id(req.channel_id, media_pkg_id, video_chk)

            manifest = PublishManifest(
                manifest_id=manifest_id,
                channel_id=req.channel_id,
                media_package_id=media_pkg_id,
                release_manifest_id=pkg.get("release_manifest_id", "rm_unknown"),
                experiment_id=pkg.get("experiment_id", "exp_unknown"),
                experiment_family_id=pkg.get("experiment_family_id", "fam_unknown"),
                content_arm_id=pkg.get("content_arm_id", "arm_unknown"),
                content_genome_id=pkg.get("content_genome_id", "gen_unknown"),
                production_directive_id=pkg.get("production_directive_id", "dir_unknown"),
                brand_id=pkg.get("brand_id", "brand_neon"),
                idempotency_key=idem_key,
                video_master_uri=video_uri,
                video_checksum=video_chk,
                video_duration_seconds=duration,
                thumbnail_uri=thumb_uri,
                thumbnail_checksum=thumb_chk,
                title=title,
                description=description,
                tags=tags,
                category_id="10",
                privacy_status=PrivacyStatus.PRIVATE,
                publish_at=req.publish_at,
                contains_synthetic_media=True,
                made_for_kids=False,
                playlist_id="PL_neon_official",
                playlist_title="Neon Records Official Playlist",
                metadata_version="v1",
                created_at=now,
            )
            insert_publish_manifest(session, manifest)
            session.commit()

            # 4. Execute Publishing Engine
            engine = YouTubePublishingEngine(yt_client)
            result = engine.execute_publish(
                session,
                manifest,
                operating_mode=OperatingMode(req.operating_mode),
            )

            vid_id = result.attempt.youtube_video_id
            vid_url = result.publication_record.video_url if result.publication_record else None

            return PublishResponse(
                attempt_id=result.attempt.attempt_id,
                manifest_id=manifest.manifest_id,
                state=result.attempt.state.value,
                youtube_video_id=vid_id,
                video_url=vid_url,
                error=result.error,
            )

    @router.get("/attempts/{attempt_id}", response_model=AttemptStatusResponse, dependencies=[Depends(auth_dependency)])
    def get_attempt(attempt_id: str) -> AttemptStatusResponse:
        with session_factory() as session:
            attempt = get_publish_attempt_by_id(session, attempt_id)
            if not attempt:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(code="attempt_not_found", message=f"Attempt {attempt_id} not found").model_dump(),
                )
            return AttemptStatusResponse(
                attempt_id=attempt.attempt_id,
                manifest_id=attempt.manifest_id,
                channel_id=attempt.channel_id,
                state=attempt.state.value,
                operating_mode=attempt.operating_mode.value,
                youtube_video_id=attempt.youtube_video_id,
                bytes_uploaded=attempt.bytes_uploaded,
                total_bytes=attempt.total_bytes,
                error_code=attempt.error_code,
                error_message=attempt.error_message,
                created_at=attempt.created_at,
                updated_at=attempt.updated_at,
                completed_at=attempt.completed_at,
            )

    @router.post("/reconcile/{attempt_id}", response_model=ReconcileResponse, dependencies=[Depends(auth_dependency)])
    def reconcile_attempt(attempt_id: str) -> ReconcileResponse:
        with session_factory() as session:
            attempt = get_publish_attempt_by_id(session, attempt_id)
            if not attempt:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(code="attempt_not_found", message=f"Attempt {attempt_id} not found").model_dump(),
                )
            if not attempt.youtube_video_id:
                raise HTTPException(
                    status_code=400,
                    detail=ErrorShape(code="not_uploaded", message=f"Attempt {attempt_id} has no remote video ID").model_dump(),
                )

            remote = yt_client.get_video(attempt.youtube_video_id) or {}
            return ReconcileResponse(
                attempt_id=attempt.attempt_id,
                state=attempt.state.value,
                youtube_video_id=attempt.youtube_video_id,
                remote_status=remote.get("status", {}),
            )

    return router

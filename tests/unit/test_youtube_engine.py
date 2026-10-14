"""Unit tests for YouTubePublishingEngine state machine and idempotency (P7 §6, §11, §12, Ruling R9)."""

from __future__ import annotations

from datetime import UTC, datetime

from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.enums import OperatingMode, PrivacyStatus, PublishState
from youtube_publishing.domain.ids import build_idempotency_key, build_manifest_id
from youtube_publishing.domain.models import PublishManifest
from youtube_publishing.engine import YouTubePublishingEngine
from youtube_publishing.storage.repositories import (
    insert_publish_manifest,
)

from tests.support.youtube_db import youtube_db_session


def _sample_manifest(*, publish_at: datetime | None = None) -> PublishManifest:
    now = datetime.now(UTC)
    channel_id = "UC_neon_official"
    media_pkg_id = "pkg_synth_001"
    video_chk = "chk_vid_synth_001"
    manifest_id = build_manifest_id(channel_id, media_pkg_id, video_chk)
    idem_key = build_idempotency_key(channel_id, media_pkg_id, video_chk)

    return PublishManifest(
        manifest_id=manifest_id,
        channel_id=channel_id,
        media_package_id=media_pkg_id,
        release_manifest_id="rm_synth_001",
        experiment_id="exp_synth_001",
        experiment_family_id="fam_synth_001",
        content_arm_id="arm_synth_001",
        content_genome_id="gen_synth_001",
        production_directive_id="dir_synth_001",
        brand_id="brand_neon",
        idempotency_key=idem_key,
        video_master_uri="media/video/synth_001.mp4",
        video_checksum=video_chk,
        video_duration_seconds=240.0,
        thumbnail_uri="media/thumb/synth_001.jpg",
        thumbnail_checksum="chk_thumb_001",
        title="Midnight Neon Drive | Synthwave Chill",
        description="Atmospheric synthwave and chillwave beats for late night drives.",
        tags=["synthwave", "chillwave", "night drive"],
        category_id="10",
        privacy_status=PrivacyStatus.PRIVATE,
        publish_at=publish_at,
        contains_synthetic_media=True,
        made_for_kids=False,
        playlist_id="PL_synth_favorites",
        metadata_version="v1",
        created_at=now,
    )


def test_engine_publishes_end_to_end_successfully() -> None:
    manifest = _sample_manifest()
    client = MockYouTubeClient()
    engine = YouTubePublishingEngine(client, chunk_size=512)

    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)

        result = engine.execute_publish(
            session,
            manifest,
            video_data=b"0" * 2048,
            thumbnail_data=b"IMAGE",
            operating_mode=OperatingMode.DRY_RUN,
        )

        assert result.is_success
        assert result.attempt.state == PublishState.VERIFIED
        assert result.attempt.youtube_video_id is not None
        assert result.attempt.thumbnail_uploaded
        assert result.attempt.youtube_playlist_item_id is not None
        assert result.publication_record is not None
        assert result.publication_record.youtube_video_id == result.attempt.youtube_video_id

        # Verify Hop 2 event carries all R9 identity fields
        event = result.event
        assert event is not None
        assert event.event == "youtube_video_published"
        assert event.lineage.experiment_family_id == "fam_synth_001"
        assert event.lineage.content_genome_id == "gen_synth_001"
        assert event.lineage.production_directive_id == "dir_synth_001"
        assert event.media_package_id == "pkg_synth_001"


def test_engine_idempotency_prevents_duplicate_remote_upload() -> None:
    manifest = _sample_manifest()
    client = MockYouTubeClient()
    engine = YouTubePublishingEngine(client, chunk_size=1024)

    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)

        # First execution
        res1 = engine.execute_publish(session, manifest, video_data=b"0" * 1024)
        assert res1.is_success
        first_video_id = res1.attempt.youtube_video_id
        first_calls_count = len(client.recorded_calls)

        # Second execution with exact same package / idempotency key
        res2 = engine.execute_publish(session, manifest, video_data=b"0" * 1024)
        assert res2.is_success
        assert res2.attempt.youtube_video_id == first_video_id
        # No extra remote upload calls made!
        assert len(client.recorded_calls) == first_calls_count


def test_engine_handles_auth_failure_permanently() -> None:
    manifest = _sample_manifest()
    client = MockYouTubeClient(fail_auth=True)
    engine = YouTubePublishingEngine(client)

    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        res = engine.execute_publish(session, manifest)
        assert not res.is_success
        assert res.attempt.state == PublishState.FAILED_PERMANENT
        assert "OAuth credential expired" in str(res.error)


def test_engine_handles_transient_failure_retryably() -> None:
    manifest = _sample_manifest()
    client = MockYouTubeClient(transient_failures_remaining=1)
    engine = YouTubePublishingEngine(client)

    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        res = engine.execute_publish(session, manifest)
        assert not res.is_success
        assert res.attempt.state == PublishState.FAILED_RETRYABLE
        assert res.attempt.retry_count == 1

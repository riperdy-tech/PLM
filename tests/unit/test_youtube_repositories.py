"""Unit tests for P7 storage repositories against in-memory SQLite."""

from __future__ import annotations

from datetime import UTC, datetime

from youtube_publishing.domain.enums import OperatingMode, PrivacyStatus, PublishState
from youtube_publishing.domain.models import (
    CandidateSelection,
    ChannelProfile,
    PublishAttempt,
    PublishManifest,
    YouTubePublicationRecord,
)
from youtube_publishing.storage.repositories import (
    get_channel_profile_by_channel_id,
    get_channel_profile_by_id,
    get_publication_record_by_package_id,
    get_publication_record_by_video_id,
    get_publish_attempt_by_id,
    get_publish_manifest_by_id,
    get_publish_manifest_by_idempotency_key,
    insert_channel_profile,
    insert_publication_record,
    insert_publish_attempt,
    insert_publish_manifest,
    update_publish_attempt,
)

from tests.support.clearance import test_track_refs
from tests.support.youtube_db import youtube_db_session


def test_channel_profile_repository_roundtrip() -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    profile = ChannelProfile(
        id="prof_01",
        channel_id="UC_01",
        channel_name="Test Channel",
        brand_id="brand_neon",
        oauth_credential_ref="sec_oauth_01",
        default_privacy_mode=PrivacyStatus.PRIVATE,
        active=True,
        operating_mode=OperatingMode.DRY_RUN,
        created_at=now,
        updated_at=now,
    )

    with youtube_db_session() as session:
        insert_channel_profile(session, profile)
        by_id = get_channel_profile_by_id(session, "prof_01")
        assert by_id is not None
        assert by_id.channel_name == "Test Channel"

        by_chan = get_channel_profile_by_channel_id(session, "UC_01")
        assert by_chan is not None
        assert by_chan.id == "prof_01"


def test_manifest_and_attempt_repositories_roundtrip() -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    manifest = PublishManifest(
        manifest_id="pub_man_01",
        channel_id="UC_01",
        media_package_id="pkg_01",
        release_manifest_id="rm_01",
        experiment_id="exp_01",
        experiment_family_id="fam_01",
        content_arm_id="arm_01",
        content_genome_id="gen_01",
        production_directive_id="dir_01",
        brand_id="brand_neon",
        idempotency_key="idem_01",
        video_master_uri="media/video/01.mp4",
        video_checksum="chk_vid_01",
        video_duration_seconds=180.0,
        thumbnail_uri="media/thumb/01.jpg",
        thumbnail_checksum="chk_thumb_01",
        title="Title Test",
        description="Description Test",
        tags=["tag1", "tag2"],
        category_id="10",
        privacy_status=PrivacyStatus.PRIVATE,
        contains_synthetic_media=True,
        made_for_kids=False,
        metadata_version="v1",
        selection=CandidateSelection(
            method="explicit", metadata_variant_id="meta_b", thumbnail_id="thumb_b",
            eligible_metadata_ids=("meta_a", "meta_b"),
            eligible_thumbnail_ids=("thumb_a", "thumb_b"),
        ),
        created_at=now,
        tracks=test_track_refs(),
    )

    attempt = PublishAttempt(
        attempt_id="att_01_01",
        manifest_id="pub_man_01",
        channel_id="UC_01",
        idempotency_key="idem_01",
        state=PublishState.READY,
        operating_mode=OperatingMode.DRY_RUN,
        created_at=now,
        updated_at=now,
    )

    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        insert_publish_attempt(session, attempt)

        m_fetch = get_publish_manifest_by_id(session, "pub_man_01")
        assert m_fetch is not None
        assert m_fetch.title == "Title Test"
        assert m_fetch.selection == manifest.selection
        assert m_fetch.selection is not None
        assert m_fetch.selection.thumbnail_id == "thumb_b"

        m_idem = get_publish_manifest_by_idempotency_key(session, "idem_01")
        assert m_idem is not None
        assert m_idem.manifest_id == "pub_man_01"

        a_fetch = get_publish_attempt_by_id(session, "att_01_01")
        assert a_fetch is not None
        assert a_fetch.state == PublishState.READY

        # Update attempt
        update_publish_attempt(
            session,
            "att_01_01",
            state=PublishState.UPLOADING,
            bytes_uploaded=500,
            youtube_video_id="yt_vid_123",
        )
        a_updated = get_publish_attempt_by_id(session, "att_01_01")
        assert a_updated is not None
        assert a_updated.state == PublishState.UPLOADING
        assert a_updated.bytes_uploaded == 500
        assert a_updated.youtube_video_id == "yt_vid_123"


def test_publication_record_repository_roundtrip() -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    record = YouTubePublicationRecord(
        publication_record_id="pub_rec_01",
        youtube_video_id="yt_01",
        channel_id="UC_01",
        publish_attempt_id="att_01_01",
        media_package_id="pkg_01",
        release_manifest_id="rm_01",
        experiment_id="exp_01",
        experiment_family_id="fam_01",
        content_arm_id="arm_01",
        content_genome_id="gen_01",
        production_directive_id="dir_01",
        title="Published Video",
        privacy_status=PrivacyStatus.PRIVATE,
        contains_synthetic_media=True,
        published_at=now,
        video_url="https://youtube.com/watch?v=yt_01",
        created_at=now,
        updated_at=now,
    )

    with youtube_db_session() as session:
        insert_publication_record(session, record)

        rec_vid = get_publication_record_by_video_id(session, "yt_01")
        assert rec_vid is not None
        assert rec_vid.title == "Published Video"

        rec_pkg = get_publication_record_by_package_id(session, "pkg_01")
        assert rec_pkg is not None
        assert rec_pkg.youtube_video_id == "yt_01"

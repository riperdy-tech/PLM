"""Unit tests for YouTubePublishingEngine state machine and idempotency (P7 §6, §11, §12, Ruling R9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plm_platform.clock import FixedClock
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.enums import OperatingMode, PrivacyStatus, PublishState
from youtube_publishing.domain.ids import build_idempotency_key, build_manifest_id
from youtube_publishing.domain.models import PublishManifest
from youtube_publishing.engine import YouTubePublishingEngine
from youtube_publishing.storage.repositories import (
    insert_publish_manifest,
)

from tests.support.clearance import p5_clearing_test_track, test_track_refs
from tests.support.youtube_db import youtube_db_session

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _sample_manifest(*, publish_at: datetime | None = None) -> PublishManifest:
    now = T0
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
        tracks=test_track_refs(),
    )


def test_engine_publishes_end_to_end_successfully() -> None:
    manifest = _sample_manifest()
    client = MockYouTubeClient()
    clock = FixedClock(T0)
    engine = YouTubePublishingEngine(client, clock=clock, chunk_size=512, clearance=p5_clearing_test_track())

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
        # Every recorded instant is the injected clock's, never the wall clock's.
        assert result.attempt.created_at == T0
        assert result.attempt.completed_at == T0
        assert result.publication_record.published_at == T0

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
    engine = YouTubePublishingEngine(client, clock=FixedClock(T0), chunk_size=1024, clearance=p5_clearing_test_track())

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
    engine = YouTubePublishingEngine(client, clock=FixedClock(T0), clearance=p5_clearing_test_track())

    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        res = engine.execute_publish(session, manifest)
        assert not res.is_success
        assert res.attempt.state == PublishState.FAILED_PERMANENT
        assert "OAuth credential expired" in str(res.error)


def test_engine_handles_transient_failure_retryably() -> None:
    manifest = _sample_manifest()
    client = MockYouTubeClient(transient_failures_remaining=1)
    engine = YouTubePublishingEngine(client, clock=FixedClock(T0), clearance=p5_clearing_test_track())

    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        res = engine.execute_publish(session, manifest)
        assert not res.is_success
        assert res.attempt.state == PublishState.FAILED_RETRYABLE
        assert res.attempt.retry_count == 1


def test_engine_stamps_come_from_the_injected_clock_and_replay() -> None:
    """Two runs under the same fixed clock produce identical records and events."""
    published_at = T0 + timedelta(minutes=5)

    def run() -> tuple[object, object]:
        clock = FixedClock(T0)
        manifest = _sample_manifest()
        engine = YouTubePublishingEngine(MockYouTubeClient(), clock=clock, chunk_size=512, clearance=p5_clearing_test_track())
        with youtube_db_session() as session:
            insert_publish_manifest(session, manifest)
            clock.advance(timedelta(minutes=5))
            result = engine.execute_publish(
                session, manifest, video_data=b"0" * 1024, thumbnail_data=b"IMAGE"
            )
        assert result.is_success
        assert result.attempt.created_at == published_at
        assert result.attempt.updated_at == published_at
        assert result.publication_record is not None
        assert result.event is not None
        return result.publication_record, result.event.to_wire()

    first_record, first_event = run()
    second_record, second_event = run()
    assert first_record == second_record
    assert first_event == second_event


def _blocked(clearance: object, manifest: PublishManifest) -> tuple[object, MockYouTubeClient]:
    client = MockYouTubeClient()
    engine = YouTubePublishingEngine(client, clock=FixedClock(T0), chunk_size=512,
                                     clearance=clearance)  # type: ignore[arg-type]
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        result = engine.execute_publish(session, manifest, video_data=b"0" * 2048,
                                        thumbnail_data=b"IMAGE",
                                        operating_mode=OperatingMode.DRY_RUN)
    return result, client


def test_a_track_p5_quarantined_never_reaches_youtube() -> None:
    """Proves (Gate R4, alternate path P7 direct): the package says ready, P5 says
    QUARANTINED; the attempt is BLOCKED_PREFLIGHT with release_not_cleared and the client
    is never called."""
    from tests.support.clearance import TEST_TRACK_ARTIFACT, TEST_TRACK_CANDIDATE, p5_holding
    from tests.support.seams import release_manifest

    quarantined = p5_holding(release_manifest(
        candidate_id=TEST_TRACK_CANDIDATE, canonical_artifact_id=TEST_TRACK_ARTIFACT,
        disposition="QUARANTINED"))
    result, client = _blocked(quarantined, _sample_manifest())
    assert result.attempt.state == PublishState.BLOCKED_PREFLIGHT  # type: ignore[attr-defined]
    assert result.attempt.error_code == "release_not_cleared"  # type: ignore[attr-defined]
    assert client.recorded_calls == []


def test_an_open_hold_on_the_recording_stops_the_upload() -> None:
    """Proves: a dispute raised after clearance (an open case on the recording) blocks."""
    from plm_platform.clearance import TrackRef, recording_subject, verify_release_clearance

    from tests.support.clearance import TEST_TRACK_ARTIFACT, TEST_TRACK_CANDIDATE
    from tests.support.seams import release_manifest

    manifest = release_manifest(candidate_id=TEST_TRACK_CANDIDATE,
                                canonical_artifact_id=TEST_TRACK_ARTIFACT)

    class P5:
        def release_manifest(self, candidate_id: str) -> object:
            return manifest if candidate_id == TEST_TRACK_CANDIDATE else None

    class Disputed:
        def open_hold_codes(self, subject_ref: str) -> tuple[str, ...]:
            return ("COPYRIGHT_DISPUTE",) if subject_ref == recording_subject(
                TEST_TRACK_CANDIDATE) else ()

    def verify(tracks: list[TrackRef]) -> object:
        return verify_release_clearance(tracks, manifests=P5(), holds=Disputed())  # type: ignore[arg-type]

    result, client = _blocked(verify, _sample_manifest())
    assert result.attempt.error_code == "release_not_cleared"  # type: ignore[attr-defined]
    assert "COPYRIGHT_DISPUTE" in (result.error or "")  # type: ignore[attr-defined]
    assert client.recorded_calls == []


def test_a_manifest_without_its_tracklist_is_never_cleared() -> None:
    """Proves: a manifest stored before tracks were recorded cannot be verified, so it
    is refused rather than published."""
    legacy = _sample_manifest().model_copy(update={"tracks": ()})
    result, client = _blocked(p5_clearing_test_track(), legacy)
    assert result.attempt.error_code == "release_not_cleared"  # type: ignore[attr-defined]
    assert client.recorded_calls == []

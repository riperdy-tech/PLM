"""Real-provider post-upload metadata checks; no network or live external writes."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from plm_contracts import ExecutionPolicy, ProviderMode
from plm_platform.clock import FixedClock
from plm_platform.execution import GuardDecision, WriteDecision
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.enums import OperatingMode, PublishState
from youtube_publishing.engine import YouTubePublishingEngine
from youtube_publishing.storage.repositories import insert_publish_manifest
from youtube_publishing.verification import verify_remote_video

from tests.support.clearance import p5_clearing_test_track
from tests.support.youtube_db import youtube_db_session
from tests.unit.test_youtube_engine import _sample_manifest

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def _manifest():
    return _sample_manifest().model_copy(update={
        "video_duration_seconds": 1800.0,
        "description": "0:00 Intro\n10:00 Middle\n20:00 Ending",
    })


def _resource(*, desc: str | None = None, processing: str = "succeeded",
              duration: str = "PT30M") -> dict:
    m = _manifest()
    return {
        "id": "video_test",
        "snippet": {"description": m.description if desc is None else desc},
        "contentDetails": {"duration": duration},
        "status": {"privacyStatus": "private", "uploadStatus": "processed"},
        "processingDetails": {"processingStatus": processing},
    }


def test_processed_description_can_pass_but_player_still_unverified() -> None:
    check = verify_remote_video(
        _resource(), video_id="video_test", manifest=_manifest(), now=NOW,
    )
    assert check.ready and not check.retryable
    assert "UNVERIFIED" in check.reason


def test_video_processing_pending_is_not_marked_verified() -> None:
    check = verify_remote_video(
        _resource(processing="processing"), video_id="video_test",
        manifest=_manifest(), now=NOW,
    )
    assert not check.ready and check.retryable


def test_missing_processing_status_never_passes() -> None:
    remote = _resource()
    remote.pop("processingDetails")
    check = verify_remote_video(remote, video_id="video_test", manifest=_manifest(), now=NOW)
    assert not check.ready and check.retryable


def test_returned_description_changes_or_lacks_chapters_never_pass() -> None:
    for changed in ["Album with no timestamps", "0:00 Intro\n10:00 Wrong\n20:00 Ending"]:
        check = verify_remote_video(
            _resource(desc=changed), video_id="video_test",
            manifest=_manifest(), now=NOW,
        )
        assert not check.ready and not check.retryable


def test_bad_processed_duration_and_wrong_video_id_rejected() -> None:
    check = verify_remote_video(
        _resource(duration="PT15M"), video_id="video_test",
        manifest=_manifest(), now=NOW,
    )
    assert not check.ready
    check = verify_remote_video(
        _resource(), video_id="video_other", manifest=_manifest(), now=NOW,
    )
    assert not check.ready


class FakeRealAdapter(MockYouTubeClient):
    """In-memory provider that advertises REAL only to exercise P7's real path."""

    provider_mode = ProviderMode.REAL

    def __init__(self) -> None:
        super().__init__()
        self.processing = "processing"
        self.override_description: str | None = None

    def get_video(self, video_id: str) -> dict | None:
        resource = super().get_video(video_id)
        if resource is None:
            return None
        resource["processingDetails"] = {"processingStatus": self.processing}
        resource["contentDetails"] = {"duration": "PT30M"}
        resource["status"]["uploadStatus"] = "processed"
        if self.override_description is not None:
            resource["snippet"]["description"] = self.override_description
        return resource


def _simulated_authorized_guard() -> MagicMock:
    """Test-only guard response; NOT a way to authorize real provider writes."""
    guard = MagicMock()
    guard.authorize_external_write.side_effect = lambda capability, **kwargs: GuardDecision(
        capability=capability,
        decision=WriteDecision.EXECUTE,
        reason="test-only",
        provider_mode=ProviderMode.REAL,
        policy=ExecutionPolicy.LIVE,
        idempotency_key=kwargs["idempotency_key"],
        cost_estimate=None,
        decided_at=NOW,
    )
    return guard


def test_real_path_holds_until_processed_then_resumes_same_uploaded_id() -> None:
    manifest = _manifest()
    client = FakeRealAdapter()
    guard = _simulated_authorized_guard()
    engine = YouTubePublishingEngine(
        client, clock=FixedClock(NOW), clearance=p5_clearing_test_track(),
        write_guard=guard, chunk_size=100,
    )
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        first = engine.execute_publish(
            session, manifest, video_data=b"video" * 50,
            thumbnail_data=b"THUMB", operating_mode=OperatingMode.AUTONOMOUS,
        )
        assert not first.is_success
        assert first.attempt.state is PublishState.FAILED_RETRYABLE
        assert first.attempt.error_code == "remote_processing_not_confirmed"
        assert first.attempt.youtube_video_id is not None
        assert not client.thumbnails
        assert first.event is None
        upload_count = sum(c["method"] == "upload_video_chunk" for c in client.recorded_calls)

        client.processing = "succeeded"
        second = engine.execute_publish(
            session, manifest, video_data=b"video" * 50,
            thumbnail_data=b"THUMB", operating_mode=OperatingMode.AUTONOMOUS,
        )
        assert second.is_success
        assert second.attempt.state is PublishState.VERIFIED
        assert second.attempt.youtube_video_id == first.attempt.youtube_video_id
        assert sum(c["method"] == "upload_video_chunk" for c in client.recorded_calls) == upload_count


def test_real_path_wrong_description_never_sets_thumbnail_or_success_event() -> None:
    manifest = _manifest()
    client = FakeRealAdapter()
    client.processing = "succeeded"
    client.override_description = "No album chapters here"
    engine = YouTubePublishingEngine(
        client, clock=FixedClock(NOW), clearance=p5_clearing_test_track(),
        write_guard=_simulated_authorized_guard(), chunk_size=100,
    )
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        result = engine.execute_publish(
            session, manifest, video_data=b"video" * 50,
            thumbnail_data=b"THUMB", operating_mode=OperatingMode.AUTONOMOUS,
        )
        assert not result.is_success
        assert result.attempt.state is PublishState.FAILED_PERMANENT
        assert result.attempt.error_code == "remote_metadata_mismatch"
        assert result.event is None
        assert not client.thumbnails

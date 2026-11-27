"""P7's operating mode bounds the privacy YouTube is asked for (review 2026-11-26 #32).

``private_only`` is the private smoke test: whatever the channel profile says, the upload
is private and never scheduled to go public. ``staged`` uploads private or unlisted until
an operator makes it public. Only ``autonomous`` follows the profile and the schedule.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from plm_platform.clock import FixedClock
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.enums import OperatingMode, PrivacyStatus, PublishState
from youtube_publishing.engine import PublishingEngineResult, YouTubePublishingEngine
from youtube_publishing.storage.repositories import insert_publish_manifest

from tests.support.clearance import p5_clearing_test_track
from tests.support.youtube_db import youtube_db_session
from tests.unit.test_youtube_engine import T0, _sample_manifest


def _publish(mode: OperatingMode, privacy: PrivacyStatus, *, scheduled: bool = False) -> tuple[
        dict[str, Any], PublishingEngineResult]:
    manifest = _sample_manifest(publish_at=T0 + timedelta(days=1) if scheduled else None)
    manifest = manifest.model_copy(update={"privacy_status": privacy})
    client = MockYouTubeClient()
    engine = YouTubePublishingEngine(client, clock=FixedClock(T0), chunk_size=512,
                                     clearance=p5_clearing_test_track())
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        result = engine.execute_publish(session, manifest, operating_mode=mode)
    (sent,) = client.sessions.values()
    return sent["status"], result


@pytest.mark.parametrize("privacy", list(PrivacyStatus))
def test_private_only_always_uploads_private_and_unscheduled(privacy: PrivacyStatus) -> None:
    status, result = _publish(OperatingMode.PRIVATE_ONLY, privacy, scheduled=True)
    assert status["privacyStatus"] == "private"
    assert "publishAt" not in status
    assert result.attempt.state is PublishState.VERIFIED
    assert result.publication_record is not None
    assert result.publication_record.privacy_status is PrivacyStatus.PRIVATE


@pytest.mark.parametrize(("privacy", "sent"), [
    (PrivacyStatus.PUBLIC, "private"), (PrivacyStatus.UNLISTED, "unlisted"),
    (PrivacyStatus.PRIVATE, "private")])
def test_staged_never_uploads_public(privacy: PrivacyStatus, sent: str) -> None:
    status, result = _publish(OperatingMode.STAGED, privacy, scheduled=True)
    assert status["privacyStatus"] == sent
    assert "publishAt" not in status
    assert result.publication_record is not None
    assert result.publication_record.privacy_status.value == sent


def test_autonomous_follows_the_profile_and_schedule() -> None:
    status, result = _publish(OperatingMode.AUTONOMOUS, PrivacyStatus.PRIVATE, scheduled=True)
    assert status["privacyStatus"] == "private" and "publishAt" in status
    assert result.attempt.state is PublishState.SCHEDULED
    status, _ = _publish(OperatingMode.AUTONOMOUS, PrivacyStatus.PUBLIC)
    assert status["privacyStatus"] == "public"

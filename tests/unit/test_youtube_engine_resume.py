"""P7 resumes its own unfinished writes against the real guard and ledger (review 2026-11-26).

A real-mode retry used to be refused forever: the ledger answers "replayed" for a key it
already holds, and the guard turned that into DENY ``budget_reservation_replayed``. A
failed chunk or a lost playlist response then left a live video with no publication
record. Now a write whose reservation is still only held is continued, by asking YouTube
what it already has; a committed one is never written again.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts import ExecutionPolicy, ProviderMode
from plm_contracts.money import Money
from plm_platform.clock import FixedClock
from plm_platform.execution import (
    RESUMED_RESERVATION_REASON,
    ExecutionSettings,
    ExternalWriteGuard,
    WriteAuthorization,
    WriteDecision,
)
from plm_platform.ledger import BudgetLedger, EntryStatus, PersistentBudgetGate
from youtube_publishing import engine as engine_module
from youtube_publishing.client.base import (
    YouTubeClientError,
    YouTubeQuotaExceededError,
    YouTubeTransientError,
)
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.enums import OperatingMode, PublishState
from youtube_publishing.engine import (
    CAPABILITY_PLAYLIST,
    CAPABILITY_THUMBNAIL,
    CAPABILITY_UPLOAD,
    YouTubePublishingEngine,
)
from youtube_publishing.storage.repositories import insert_publish_manifest
from youtube_publishing.verification import RemoteVideoCheck

from tests.support.clearance import p5_clearing_test_track
from tests.support.ops_db import create_ops_engine, ops_session_factory
from tests.support.youtube_db import youtube_db_session
from tests.unit.test_youtube_engine import T0, _sample_manifest

CAPABILITIES = (CAPABILITY_UPLOAD, CAPABILITY_THUMBNAIL, CAPABILITY_PLAYLIST)
VIDEO = b"v" * 2048


class RealModeClient(MockYouTubeClient):
    """The mock's in-memory YouTube, decided by the guard as a real (paid) client."""

    provider_mode = ProviderMode.REAL

    def __init__(self) -> None:
        super().__init__()
        self.fail_chunk_at: int | None = None
        self.lose_playlist_response = False
        self.inserted_items = 0
        self.session_error: Exception | None = None
        self.thumbnail_error: Exception | None = None

    def insert_resumable_session(self, **kwargs: Any) -> str:
        if self.session_error is not None:
            error, self.session_error = self.session_error, None
            raise error
        return super().insert_resumable_session(**kwargs)

    def set_thumbnail(self, video_id: str, thumbnail_bytes: bytes) -> dict[str, Any]:
        if self.thumbnail_error is not None:
            error, self.thumbnail_error = self.thumbnail_error, None
            raise error
        return super().set_thumbnail(video_id, thumbnail_bytes)

    def upload_video_chunk(self, **kwargs: Any) -> dict[str, Any] | None:
        if kwargs["start_byte"] == self.fail_chunk_at:
            self.fail_chunk_at = None
            raise YouTubeTransientError("connection reset (simulated)")
        return super().upload_video_chunk(**kwargs)

    def insert_playlist_item(self, playlist_id: str, video_id: str,
                             position: int = 0) -> dict[str, Any]:
        self.inserted_items += 1
        item = super().insert_playlist_item(playlist_id, video_id, position)
        if self.lose_playlist_response:
            self.lose_playlist_response = False
            raise YouTubeTransientError("response lost after the insert (simulated)")
        return item


def _ledger() -> BudgetLedger:
    ledger = BudgetLedger(ops_session_factory(create_ops_engine()), currency="USD")
    start = datetime(2026, 9, 1, tzinfo=UTC)
    ledger.set_ceiling(limit=Money.of("10", "USD"), period_start=start,
                       period_end=datetime(2026, 12, 1, tzinfo=UTC), set_by="unit-test", now=T0)
    for capability in CAPABILITIES:
        ledger.set_rate(capability=capability, cost=Money.of("0", "USD"),
                        effective_from=start, set_by="unit-test", now=T0)
    return ledger


def _engine(client: RealModeClient, ledger: BudgetLedger) -> YouTubePublishingEngine:
    guard = ExternalWriteGuard(
        ExecutionSettings(policies={c: ExecutionPolicy.LIVE for c in CAPABILITIES}),
        authorizations=[WriteAuthorization(c, granted_by="owner", granted_at=T0)
                        for c in CAPABILITIES],
        budget=PersistentBudgetGate(ledger, clock=FixedClock(T0)),
    )
    return YouTubePublishingEngine(client, clock=FixedClock(T0), chunk_size=512,
                                   clearance=p5_clearing_test_track(), write_guard=guard)


@pytest.fixture(autouse=True)
def _remote_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "verify_remote_video",
                        lambda *a, **k: RemoteVideoCheck(True, False, "ready (test)"))


def _publish(engine: YouTubePublishingEngine, session: Any, manifest: Any) -> Any:
    return engine.execute_publish(session, manifest, video_data=VIDEO,
                                  thumbnail_data=b"IMAGE",
                                  operating_mode=OperatingMode.AUTONOMOUS)


def test_a_failed_chunk_resumes_from_what_youtube_holds_and_uploads_once() -> None:
    manifest = _sample_manifest()
    client, ledger = RealModeClient(), _ledger()
    client.fail_chunk_at = 1024
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        first = _publish(_engine(client, ledger), session, manifest)
        assert first.attempt.state is PublishState.FAILED_RETRYABLE
        upload_key = YouTubePublishingEngine.write_key(manifest, CAPABILITY_UPLOAD)
        assert ledger.entry(CAPABILITY_UPLOAD, upload_key).status is EntryStatus.RESERVED

        retry_engine = _engine(client, ledger)
        second = _publish(retry_engine, session, manifest)

    assert second.is_success and second.publication_record is not None
    assert retry_engine.decisions[0].reason == RESUMED_RESERVATION_REASON
    assert len(client.sessions) == 1 and len(client.videos) == 1  # one upload, resumed
    assert ledger.entry(CAPABILITY_UPLOAD, upload_key).status is EntryStatus.COMMITTED


def test_a_lost_playlist_response_finds_the_item_and_skips_done_writes() -> None:
    manifest = _sample_manifest()
    client, ledger = RealModeClient(), _ledger()
    client.lose_playlist_response = True
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        first = _publish(_engine(client, ledger), session, manifest)
        assert first.attempt.state is PublishState.FAILED_RETRYABLE
        assert first.attempt.thumbnail_uploaded

        retry_engine = _engine(client, ledger)
        second = _publish(retry_engine, session, manifest)

    assert second.is_success and second.attempt.youtube_playlist_item_id
    # Upload and thumbnail were done: not decided again; the playlist was found, not re-added.
    assert [d.capability for d in retry_engine.decisions] == [CAPABILITY_PLAYLIST]
    assert client.inserted_items == 1 and len(client.playlist_items) == 1
    receipts = {r["capability"]: r for r in retry_engine.write_receipts(manifest, second)}
    assert receipts[CAPABILITY_UPLOAD]["reason"] == "performed_earlier"
    assert receipts[CAPABILITY_THUMBNAIL]["reason"] == "performed_earlier"
    for capability in CAPABILITIES:
        key = YouTubePublishingEngine.write_key(manifest, capability)
        assert ledger.entry(capability, key).status is EntryStatus.COMMITTED


def test_a_committed_write_is_never_resumed() -> None:
    ledger = _ledger()
    guard = _engine(RealModeClient(), ledger).write_guard

    def decide(resuming: bool) -> Any:
        return guard.authorize_external_write(
            CAPABILITY_UPLOAD, provider_mode=ProviderMode.REAL, idempotency_key="k",
            now=T0, resuming=resuming)

    first = decide(False)
    assert first.decision is WriteDecision.EXECUTE
    assert decide(False).reason == "budget_reservation_replayed"  # not the caller's resume
    assert decide(True).reason == RESUMED_RESERVATION_REASON  # held: may be continued
    guard.settle_external_write(first, performed=True)
    again = decide(True)
    assert again.decision is WriteDecision.DENY
    assert again.reason == "budget_reservation_replayed"


@pytest.mark.parametrize("error", [
    YouTubeQuotaExceededError("quotaExceeded (simulated)", refused=True),
    YouTubeTransientError("HTTP 503 before any session (simulated)"),
])
def test_an_upload_that_never_started_releases_its_reservation(error: Exception) -> None:
    """A refused (4xx) session request, or any failure before a session existed, sent no
    video: the reservation is released, so the retry is funded again, not wedged (#31)."""
    manifest = _sample_manifest()
    client, ledger = RealModeClient(), _ledger()
    client.session_error = error
    upload_key = YouTubePublishingEngine.write_key(manifest, CAPABILITY_UPLOAD)
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        first = _publish(_engine(client, ledger), session, manifest)
        assert first.attempt.state is PublishState.FAILED_RETRYABLE
        assert ledger.entry(CAPABILITY_UPLOAD, upload_key).status is EntryStatus.RELEASED

        retry_engine = _engine(client, ledger)
        second = _publish(retry_engine, session, manifest)

    assert second.is_success
    assert retry_engine.decisions[0].reason == "authorized"
    assert ledger.entry(CAPABILITY_UPLOAD, upload_key).status is EntryStatus.COMMITTED


def test_a_resume_after_the_upload_needs_no_media_and_clears_the_old_error() -> None:
    """A processing poll must not re-read the master (review P2: P7), and a VERIFIED
    attempt must not keep the error code of the failure it recovered from."""
    manifest = _sample_manifest()
    client, ledger = RealModeClient(), _ledger()
    client.lose_playlist_response = True
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        first = _publish(_engine(client, ledger), session, manifest)
        assert first.attempt.error_code is not None
        second = _engine(client, ledger).execute_publish(
            session, manifest, video_data=None, thumbnail_data=None,
            operating_mode=OperatingMode.AUTONOMOUS)
    assert second.is_success, second.error
    assert second.attempt.state is PublishState.VERIFIED
    assert (second.attempt.error_code, second.attempt.error_message) == (None, None)


def test_a_refused_thumbnail_is_released_and_an_unknown_one_stays_held() -> None:
    manifest = _sample_manifest()
    client, ledger = RealModeClient(), _ledger()
    thumb_key = YouTubePublishingEngine.write_key(manifest, CAPABILITY_THUMBNAIL)
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        client.thumbnail_error = YouTubeClientError("HTTP 400 (simulated)", refused=True)
        _publish(_engine(client, ledger), session, manifest)
        assert ledger.entry(CAPABILITY_THUMBNAIL, thumb_key).status is EntryStatus.RELEASED

        client.thumbnail_error = YouTubeTransientError("HTTP 503 (simulated)")
        _publish(_engine(client, ledger), session, manifest)
        assert ledger.entry(CAPABILITY_THUMBNAIL, thumb_key).status is EntryStatus.RESERVED

        final = _publish(_engine(client, ledger), session, manifest)
    assert final.is_success
    assert ledger.entry(CAPABILITY_THUMBNAIL, thumb_key).status is EntryStatus.COMMITTED

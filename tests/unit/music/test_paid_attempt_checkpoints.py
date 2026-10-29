"""B03/B04: a paid provider call stays on record when the request later fails.

SQLite and fake providers only. Proves the P4 engine and API persist the
SUBMITTED attempt before a REAL call and its settled outcome right after it,
so a later failure in the same request cannot erase the provider job ID.
It does not prove anything about a real provider's billing.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.execution import ExternalWriteGuard
from sqlalchemy.orm import Session

from services.music_factory.api.app import create_app
from services.music_factory.config import load_music_factory_settings
from services.music_factory.domain.enums import AttemptStatus, PlanStatus
from services.music_factory.domain.models import GenerationRequest, GenerationResponse
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.storage.artifacts import (
    ArtifactStorageUnavailable,
    InMemoryArtifactStorage,
)
from services.music_factory.storage.repositories import (
    list_candidates_by_track,
    list_generation_attempts,
)
from tests.support.music_db import create_music_engine, music_session
from tests.unit.music.test_write_guard_and_idempotency import (
    ENV,
    CountingRealProvider,
    RecordingBudget,
    _live_guard,
    _spec,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
TOKEN = ENV["PLM_SERVICE_TOKEN"]


class SecondCallRaises(CountingRealProvider):
    """Paid success with a real job ID, then a transport error on the next call."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        if self.calls >= 1:
            self.calls += 1
            raise ConnectionError("transport failed after possible provider acceptance")
        return super().generate(request)


class FailingStorage(InMemoryArtifactStorage):
    def put_artifact(self, **kwargs: Any) -> str:
        raise ArtifactStorageUnavailable("object store down")


def _execute(engine_db: Any, provider: Any, *, storage: Any = None,
             checkpoint: bool = True) -> Any:
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(clock=FixedClock(NOW), storage=storage or InMemoryArtifactStorage(),
                              write_guard=_live_guard(RecordingBudget()))
    session: Session = music_session(engine_db)
    try:
        return engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec, provider_adapter=provider,
            checkpoint=session.commit if checkpoint else None,
        ), plan
    finally:
        session.rollback()
        session.close()


def _attempts(engine_db: Any, plan_id: str) -> list[Any]:
    with music_session(engine_db) as session:
        return list_generation_attempts(session, plan_id)


def test_a_later_transport_error_keeps_the_earlier_paid_job_and_the_submission() -> None:
    db = create_music_engine(static_pool=True)
    provider = SecondCallRaises()
    try:
        with pytest.raises(ConnectionError):
            _execute(db, provider)
        spec = adapt_track_spec(_spec())
        plan, _ = plan_generation(spec, created_at=NOW)
        attempts = sorted(_attempts(db, plan.plan_id), key=lambda a: a.attempt_id)
        assert [(a.status, bool(a.provider_job_id.strip())) for a in attempts] == [
            (AttemptStatus.SUCCEEDED, True), (AttemptStatus.SUBMITTED, False)]
        with music_session(db) as session:
            # The first variant's candidate committed with the next checkpoint.
            assert [c.source_attempt_id for c in list_candidates_by_track(
                session, plan.track_spec_id)] == [attempts[0].attempt_id]
        # A restart must not pay again for the unknown second submission.
        (replay, _), calls = _execute(db, provider), provider.calls
        assert calls == 2
        assert replay.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in replay.errors
    finally:
        db.dispose()


def test_storage_failure_after_a_paid_success_keeps_the_provider_job_id() -> None:
    db = create_music_engine(static_pool=True)
    provider = CountingRealProvider()
    try:
        with pytest.raises(ArtifactStorageUnavailable):
            _execute(db, provider, storage=FailingStorage())
        spec = adapt_track_spec(_spec())
        plan, _ = plan_generation(spec, created_at=NOW)
        attempts = _attempts(db, plan.plan_id)
        assert len(attempts) == 1
        assert attempts[0].status is AttemptStatus.SUCCEEDED
        assert attempts[0].provider_job_id.strip()
        assert attempts[0].credit_receipt_ref == "fixture-credit-receipt-1"
        with music_session(db) as session:
            assert list_candidates_by_track(session, plan.track_spec_id) == []
        replay, _ = _execute(db, provider)
        assert provider.calls == 1  # SUCCEEDED without a candidate: reconcile, never rebuy
        assert "provider_job_reconciliation_required" in replay.errors
    finally:
        db.dispose()


def test_without_checkpoints_the_engine_keeps_its_single_transaction_contract() -> None:
    """In-process callers that pass no checkpoint still get all-or-nothing writes."""
    db = create_music_engine(static_pool=True)
    try:
        with pytest.raises(ConnectionError):
            _execute(db, SecondCallRaises(), checkpoint=False)
        spec = adapt_track_spec(_spec())
        plan, _ = plan_generation(spec, created_at=NOW)
        assert _attempts(db, plan.plan_id) == []
    finally:
        db.dispose()


def test_api_execute_keeps_paid_attempts_when_the_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real route wires the checkpoint: a 500 cannot erase a paid job."""
    from services.music_factory.providers.adapters import SunoAdapter
    from services.music_factory.storage.artifacts import MinioArtifactStorage
    from tests.support.fake_minio import FakeMinio

    calls: list[str] = []

    def generate(self: SunoAdapter, request: GenerationRequest) -> GenerationResponse:
        calls.append(request.track_spec_id)
        if len(calls) > 1:
            raise ConnectionError("provider transport lost")
        return GenerationResponse(
            provider_job_id="suno-clip-1", status=AttemptStatus.SUCCEEDED,
            audio_data=CountingRealProvider().generate(request).audio_data,
            mime_type="audio/wav", model_version="chirp-test",
            cost_credits=1, credit_receipt_ref="suno-receipt-1",
        )

    monkeypatch.setattr(SunoAdapter, "generate", generate)
    platform = load_settings(env=dict(ENV))
    settings = load_music_factory_settings(platform=platform, default_provider="suno")
    db = create_music_engine(static_pool=True)
    app = create_app(
        session_factory=lambda: music_session(db), settings=settings,
        storage=MinioArtifactStorage(platform, client=FakeMinio()),  # type: ignore[arg-type]
        write_guard=_live_guard(RecordingBudget()), clock=FixedClock(NOW),
    )
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    headers = {SERVICE_TOKEN_HEADER: TOKEN}
    try:
        plan_id = client.post("/music/plans", json=_spec(), headers=headers).json()["plan_id"]
        failed = client.post(f"/music/plans/{plan_id}/execute", json=_spec(), headers=headers)
        assert failed.status_code == 500, failed.text
        attempts = sorted(_attempts(db, plan_id), key=lambda a: a.attempt_id)
        assert [(a.status, a.provider_job_id) for a in attempts] == [
            (AttemptStatus.SUCCEEDED, "suno-clip-1"), (AttemptStatus.SUBMITTED, "")]
        again = client.post(f"/music/plans/{plan_id}/execute", json=_spec(), headers=headers)
        assert len(calls) == 2  # no further paid call
        assert again.status_code in {200, 409}
    finally:
        db.dispose()


def test_mock_provider_runs_are_unchanged_by_checkpoints() -> None:
    """Simulated calls are not journaled: they cost nothing and stay all-or-nothing."""
    from tests.unit.music.test_write_guard_and_idempotency import CountingMockProvider

    db = create_music_engine(static_pool=True)
    try:
        spec = adapt_track_spec(_spec())
        plan, variants = plan_generation(spec, created_at=NOW)
        engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                                  write_guard=ExternalWriteGuard())
        with music_session(db) as session:
            result = engine.execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=CountingMockProvider(), checkpoint=session.commit)
            assert result.status is PlanStatus.COMPLETED
            session.rollback()
        assert _attempts(db, plan.plan_id) == []
    finally:
        db.dispose()


def test_a_failed_settle_keeps_the_provider_job_id_in_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.music_factory.generation import engine as engine_module

    def broken_settle(session: Session, attempt: Any) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(engine_module, "settle_generation_attempt", broken_settle)
    db = create_music_engine(static_pool=True)
    try:
        with pytest.raises(RuntimeError, match="database went away") as raised:
            _execute(db, CountingRealProvider())
        assert any("left SUBMITTED" in note and "provider job id" in note
                   for note in raised.value.__notes__)
        spec = adapt_track_spec(_spec())
        plan, _ = plan_generation(spec, created_at=NOW)
        assert [a.status for a in _attempts(db, plan.plan_id)] == [AttemptStatus.SUBMITTED]
    finally:
        db.dispose()

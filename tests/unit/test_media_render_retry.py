"""B02 supervisor-gated retry INTENT, not worker liveness or real recovery."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import RenderJob
from media_factory.storage.render_claims import (
    claim_queued_render_job,
    finish_claimed_render_job,
    quarantine_stale_render_job,
    queue_reconciled_render_retry,
    queue_render_job,
)
from media_factory.storage.repositories import get_render_job

from tests.support.media_db import create_media_engine, media_session
from tests.support.render_evidence import attest_inventory, attest_termination

NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)
SHA = "a" * 64
VIDEO = "f" * 64


def _quarantine(engine: object, suffix: str = "base", *, inventory: bool = True) -> RenderJob:
    job = RenderJob.create(
        f"retry_package_{suffix}", "preview_360p_v1", input_manifest_hash=SHA,
        now=NOW,
    )
    with media_session(engine) as session:  # type: ignore[arg-type]
        assert queue_render_job(session, job)
        session.commit()
    with media_session(engine) as session:  # type: ignore[arg-type]
        assert claim_queued_render_job(
            session, job_id=job.id, worker_id="terminated-worker",
            input_manifest_hash=SHA, started_at=NOW,
        )
        assert attest_termination(session, job.id, worker_id="terminated-worker",
                                  started_at=NOW, sha=SHA, at=NOW + timedelta(minutes=5))
        assert quarantine_stale_render_job(
            session, job_id=job.id, input_manifest_hash=SHA,
            started_before=NOW, quarantined_at=NOW + timedelta(minutes=5),
            expected_worker_id="terminated-worker", expected_started_at=NOW,
        )
        if inventory:
            assert attest_inventory(session, job.id, worker_id="terminated-worker",
                                    started_at=NOW, sha=SHA, at=NOW + timedelta(minutes=5))
        session.commit()
    return job


def _retry(session: object, prior: RenderJob, **overrides: object) -> RenderJob | None:
    arguments = {
        "previous_job_id": prior.id,
        "expected_worker_id": "terminated-worker",
        "expected_started_at": NOW,
        "input_manifest_hash": SHA,
        "now": NOW + timedelta(minutes=6),
    }
    arguments.update(overrides)
    return queue_reconciled_render_retry(session, **arguments)  # type: ignore[arg-type]


def test_recovery_intent_is_new_queued_attempt_without_rendering_or_mutating_prior() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        prior = _quarantine(engine)
        with media_session(engine) as session:
            next_job = _retry(session, prior)
            assert next_job is not None
            assert next_job.attempt_count == 2 and next_job.id != prior.id
            assert next_job.job_status is RenderJobStatus.QUEUED
            stored = get_render_job(session, next_job.id)
            assert stored is not None
            assert stored.id == next_job.id and stored.attempt_count == 2
            assert stored.input_manifest_hash == SHA
            session.commit()
        with media_session(engine) as session:
            assert _retry(session, prior) is None
            old = get_render_job(session, prior.id)
            assert old is not None and old.job_status is RenderJobStatus.QUARANTINED
            assert old.worker_id == "terminated-worker"
            assert not finish_claimed_render_job(
                session, job_id=prior.id, worker_id="terminated-worker",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=NOW + timedelta(minutes=7),
                status=RenderJobStatus.COMPLETED, output_checksum=VIDEO,
            )
            assert claim_queued_render_job(
                session, job_id=next_job.id, worker_id="new-worker",
                input_manifest_hash=SHA, started_at=NOW + timedelta(minutes=6),
            )
            assert not claim_queued_render_job(
                session, job_id=next_job.id, worker_id="other-worker",
                input_manifest_hash=SHA, started_at=NOW + timedelta(minutes=6),
            )
            session.commit()
    finally:
        engine.dispose()


def test_retry_requires_persisted_artifact_inventory_not_a_flag() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        prior = _quarantine(engine, "no_inventory", inventory=False)
        with media_session(engine) as session:
            assert _retry(session, prior) is None
            with pytest.raises(TypeError):
                _retry(session, prior, orphan_artifacts_reconciled=True)
            assert attest_inventory(session, prior.id, worker_id="terminated-worker",
                                    started_at=NOW, sha=SHA, at=NOW + timedelta(minutes=5))
            assert _retry(session, prior) is not None
            session.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("override", [
    {"expected_started_at": NOW.replace(tzinfo=None)},
    {"input_manifest_hash": "bad"},
    {"max_attempts": 1},
    {"max_attempts": True},
])
def test_retry_requires_valid_inputs(
    override: dict[str, object],
) -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = _quarantine(engine)
        with media_session(engine) as session:
            with pytest.raises(ValueError):
                _retry(session, job, **override)
            assert get_render_job(session, job.id) is not None
            session.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("override", [
    {"expected_worker_id": "different-worker"},
    {"expected_started_at": NOW + timedelta(seconds=1)},
    {"input_manifest_hash": "b" * 64},
    {"now": NOW + timedelta(seconds=1)},
])
def test_stale_or_conflicting_recovery_intent_does_not_write(
    override: dict[str, object],
) -> None:
    engine = create_media_engine(static_pool=True)
    try:
        prior = _quarantine(engine)
        with media_session(engine) as session:
            assert _retry(session, prior, **override) is None
            session.rollback()
    finally:
        engine.dispose()


def test_retry_intent_obeys_outer_transaction_rollback() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        prior = _quarantine(engine)
        with media_session(engine) as session:
            draft = _retry(session, prior)
            assert draft is not None
            session.rollback()
        with media_session(engine) as session:
            assert get_render_job(session, draft.id) is None
            assert get_render_job(session, prior.id).job_status is RenderJobStatus.QUARANTINED
            assert _retry(session, prior) is not None
            session.commit()
    finally:
        engine.dispose()


def test_running_or_failed_job_never_becomes_a_retry_intent() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        running = RenderJob.create(
            "retry_running", "preview_360p_v1", input_manifest_hash=SHA, now=NOW,
        )
        with media_session(engine) as session:
            assert queue_render_job(session, running)
            assert claim_queued_render_job(
                session, job_id=running.id, worker_id="terminated-worker",
                input_manifest_hash=SHA, started_at=NOW,
            )
            session.commit()
        with media_session(engine) as session:
            assert _retry(session, running) is None
            assert finish_claimed_render_job(
                session, job_id=running.id, worker_id="terminated-worker",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=NOW + timedelta(minutes=1),
                status=RenderJobStatus.FAILED, error_code="broken_output",
            )
            assert _retry(session, running) is None
            session.commit()
    finally:
        engine.dispose()

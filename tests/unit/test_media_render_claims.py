"""SQLite-only B02 render ownership tests, not full worker recovery."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import RenderJob
from media_factory.storage.render_claims import (
    claim_queued_render_job,
    finish_claimed_render_job,
    list_stale_render_job_candidates,
    quarantine_stale_render_job,
    queue_render_job,
    render_claim_namespace,
)
from media_factory.storage.repositories import get_render_job, insert_render_job

from tests.support.media_db import create_media_engine, media_session
from tests.support.render_evidence import attest_termination

NOW = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
SHA = "a" * 64
VIDEO = "f" * 64


def test_claim_namespace_is_recomputable_and_bound_to_execution_identity() -> None:
    """A different worker or start cannot write into another run's prefix."""
    identity = {"job_id": "job-fixed", "worker_id": "worker-01",
                "input_manifest_hash": SHA, "started_at": NOW}
    first = render_claim_namespace(**identity)
    same_in_another_zone = render_claim_namespace(
        **{**identity, "started_at": NOW.astimezone(timezone(timedelta(hours=9)))},
    )
    assert len(first) == 64 and first == same_in_another_zone
    assert first != render_claim_namespace(**{**identity, "worker_id": "worker-02"})
    assert first != render_claim_namespace(**{
        **identity, "started_at": NOW + timedelta(microseconds=1),
    })
    assert first != render_claim_namespace(**{**identity, "input_manifest_hash": VIDEO})
    assert "worker-01" not in first and "job-fixed" not in first


@pytest.mark.parametrize("change", [
    {"job_id": ""}, {"worker_id": "  "},
    {"input_manifest_hash": "invalid"},
    {"started_at": NOW.replace(tzinfo=None)},
])
def test_claim_namespace_rejects_incomplete_identity(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        render_claim_namespace(**{
            "job_id": "job", "worker_id": "owner", "input_manifest_hash": SHA,
            "started_at": NOW, **change,
        })  # type: ignore[arg-type]


def test_queued_intent_must_not_survive_outer_transaction_rollback() -> None:
    """An uncommitted queue intent must disappear when its owner rolls back."""
    engine = create_media_engine(static_pool=True)
    try:
        job = RenderJob.create(
            "package_outer_rollback", "preview_360p_v1",
            input_manifest_hash=SHA, now=NOW,
        )
        with media_session(engine) as session:
            assert queue_render_job(session, job)
            session.rollback()
        with media_session(engine) as session:
            assert get_render_job(session, job.id) is None
    finally:
        engine.dispose()


def test_queue_job_idempotence_preserves_exact_input_and_terminal_state() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = RenderJob.create(
            "package_queued", "preview_360p_v1",
            input_manifest_hash=SHA, now=NOW,
        )
        with media_session(engine) as session:
            assert queue_render_job(session, job)
            session.commit()
        with media_session(engine) as session:
            assert not queue_render_job(session, job)
            with pytest.raises(ValueError, match="identity|conflict"):
                queue_render_job(session, replace(job, input_manifest_hash="b" * 64))
            with pytest.raises(ValueError, match="identity|conflict"):
                queue_render_job(session, replace(job, priority="HIGH"))
            assert claim_queued_render_job(
                session, job_id=job.id, worker_id="owner",
                input_manifest_hash=SHA, started_at=NOW,
            )
            session.commit()
        with media_session(engine) as session:
            assert not queue_render_job(session, job)
            stored = get_render_job(session, job.id)
            assert stored is not None
            assert stored.job_status is RenderJobStatus.RUNNING
            assert stored.worker_id == "owner"
    finally:
        engine.dispose()


def test_queue_refuses_unbound_identity_and_already_running_intent() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = RenderJob.create(
            "package_invalid", "preview_360p_v1",
            input_manifest_hash=SHA, now=NOW,
        )
        with media_session(engine) as session:
            for invalid in (
                replace(job, id="wrong-id"),
                replace(job, input_manifest_hash=""),
                replace(job, input_manifest_hash="wrong"),
                replace(job, job_status=RenderJobStatus.RUNNING),
                replace(job, worker_id="worker"),
                replace(job, error_code="failure"),
            ):
                with pytest.raises(ValueError):
                    queue_render_job(session, invalid)
            assert get_render_job(session, job.id) is None
    finally:
        engine.dispose()


def test_render_claim_and_finish_require_original_worker_and_input() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = RenderJob.create("package_01", "preview_360p_v1",
                               input_manifest_hash=SHA, now=NOW)
        with media_session(engine) as session:
            insert_render_job(session, job)
            session.commit()
        with media_session(engine) as session:
            assert not claim_queued_render_job(
                session, job_id=job.id, worker_id="first",
                input_manifest_hash="other", started_at=NOW)
            assert claim_queued_render_job(
                session, job_id=job.id, worker_id="first",
                input_manifest_hash=SHA, started_at=NOW)
            session.commit()
        with media_session(engine) as session:
            assert not claim_queued_render_job(
                session, job_id=job.id, worker_id="second",
                input_manifest_hash=SHA, started_at=NOW)
            params = {
                "job_id": job.id,
                "input_manifest_hash": SHA,
                "started_at": NOW,
                "finished_at": NOW + timedelta(seconds=10),
                "status": RenderJobStatus.COMPLETED,
                "output_checksum": VIDEO,
            }
            assert not finish_claimed_render_job(session, worker_id="second", **params)
            assert not finish_claimed_render_job(
                session, worker_id="first",
                **{**params, "started_at": NOW + timedelta(seconds=1)})
            assert finish_claimed_render_job(session, worker_id="first", **params)
            session.commit()
        with media_session(engine) as session:
            assert not finish_claimed_render_job(session, worker_id="first", **params)
            assert not claim_queued_render_job(
                session, job_id=job.id, worker_id="second",
                input_manifest_hash=SHA, started_at=NOW)
            stored = get_render_job(session, job.id)
            assert stored is not None
            assert stored.job_status is RenderJobStatus.COMPLETED
            assert stored.output_checksum == VIDEO
    finally:
        engine.dispose()


def test_failed_claim_records_reason_and_cannot_be_reopened() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = RenderJob.create(
            "package_02", "preview_360p_v1",
            input_manifest_hash=SHA, now=NOW,
        )
        with media_session(engine) as session:
            insert_render_job(session, job)
            session.commit()
        with media_session(engine) as session:
            assert claim_queued_render_job(
                session, job_id=job.id, worker_id="worker-a",
                input_manifest_hash=SHA, started_at=NOW,
            )
            assert finish_claimed_render_job(
                session, job_id=job.id, worker_id="worker-a",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=NOW + timedelta(seconds=15),
                status=RenderJobStatus.FAILED,
                error_code="render_timeout", error_detail="toolchain expired",
            )
            session.commit()
        with media_session(engine) as session:
            stored = get_render_job(session, job.id)
            assert stored is not None
            assert stored.job_status is RenderJobStatus.FAILED
            assert stored.error_code == "render_timeout"
            assert stored.output_checksum is None
            assert not claim_queued_render_job(
                session, job_id=job.id, worker_id="worker-b",
                input_manifest_hash=SHA, started_at=NOW,
            )
            assert not finish_claimed_render_job(
                session, job_id=job.id, worker_id="worker-a",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=NOW + timedelta(seconds=20),
                status=RenderJobStatus.COMPLETED, output_checksum=VIDEO,
            )
    finally:
        engine.dispose()


def test_stale_running_claim_is_quarantined_and_old_worker_is_fenced() -> None:
    engine = create_media_engine(static_pool=True)
    cutoff = NOW + timedelta(minutes=30)
    try:
        job = RenderJob.create("package_stale", "preview_360p_v1",
                               input_manifest_hash=SHA, now=NOW)
        with media_session(engine) as session:
            assert queue_render_job(session, job)
            session.commit()
        with media_session(engine) as session:
            assert claim_queued_render_job(
                session, job_id=job.id, worker_id="worker-lost",
                input_manifest_hash=SHA, started_at=NOW)
            session.commit()
        with media_session(engine) as session:
            # Without a persisted termination record, age and identity are not enough.
            assert not quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=NOW, quarantined_at=cutoff,
                expected_worker_id="worker-lost", expected_started_at=NOW,
            )
            assert attest_termination(session, job.id, worker_id="worker-lost",
                                      started_at=NOW, sha=SHA, at=cutoff)
            assert not quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=NOW, quarantined_at=cutoff,
                expected_worker_id="other-worker", expected_started_at=NOW,
            )
            # The same worker token is not enough to identify a run: stale
            # evidence from an earlier run must never fence a newer claim.
            assert not quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=cutoff, quarantined_at=cutoff,
                expected_worker_id="worker-lost",
                expected_started_at=NOW + timedelta(seconds=1),
            )
            with pytest.raises(ValueError, match="timezone"):
                quarantine_stale_render_job(
                    session, job_id=job.id, input_manifest_hash=SHA,
                    started_before=cutoff, quarantined_at=cutoff,
                    expected_worker_id="worker-lost",
                    expected_started_at=NOW.replace(tzinfo=None),
                )
            assert not quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash="b" * 64,
                started_before=cutoff, quarantined_at=cutoff,
                expected_worker_id="worker-lost", expected_started_at=NOW)
            assert not quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=NOW - timedelta(seconds=1), quarantined_at=cutoff,
                expected_worker_id="worker-lost", expected_started_at=NOW)
            assert quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=NOW, quarantined_at=cutoff,
                expected_worker_id="worker-lost", expected_started_at=NOW)
            session.commit()
        with media_session(engine) as session:
            assert not finish_claimed_render_job(
                session, job_id=job.id, worker_id="worker-lost",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=cutoff + timedelta(seconds=1),
                status=RenderJobStatus.COMPLETED, output_checksum=VIDEO)
            assert not claim_queued_render_job(
                session, job_id=job.id, worker_id="worker-new",
                input_manifest_hash=SHA, started_at=cutoff)
            assert not queue_render_job(session, job)
            assert not quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=cutoff, quarantined_at=cutoff,
                expected_worker_id="worker-lost", expected_started_at=NOW)
            stored = get_render_job(session, job.id)
            assert stored is not None
            assert stored.job_status is RenderJobStatus.QUARANTINED
            assert stored.worker_id == "worker-lost"
            assert stored.error_code == "render_run_stale"
            assert stored.output_checksum is None
            assert stored.finished_at is not None
    finally:
        engine.dispose()


def test_stale_quarantine_is_guarded_and_cannot_replace_completed_work() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = RenderJob.create("package_finished", "preview_360p_v1",
                               input_manifest_hash=SHA, now=NOW)
        with media_session(engine) as session:
            assert queue_render_job(session, job)
            assert not quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=NOW, quarantined_at=NOW,
                expected_worker_id="worker", expected_started_at=NOW)
            assert claim_queued_render_job(
                session, job_id=job.id, worker_id="worker",
                input_manifest_hash=SHA, started_at=NOW)
            assert attest_termination(session, job.id, worker_id="worker",
                                      started_at=NOW, sha=SHA, at=NOW)
            assert finish_claimed_render_job(
                session, job_id=job.id, worker_id="worker",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=NOW + timedelta(seconds=5),
                status=RenderJobStatus.COMPLETED, output_checksum=VIDEO)
            assert not quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=NOW, quarantined_at=NOW + timedelta(hours=1),
                expected_worker_id="worker", expected_started_at=NOW)
            for invalid_hash, before, when in (
                ("invalid", NOW, NOW),
                (SHA, NOW, NOW - timedelta(seconds=1)),
                (SHA, NOW.replace(tzinfo=None), NOW),
            ):
                with pytest.raises(ValueError):
                    quarantine_stale_render_job(
                        session, job_id=job.id, input_manifest_hash=invalid_hash,
                        started_before=before, quarantined_at=when,
                        expected_worker_id="worker", expected_started_at=NOW)
            stored = get_render_job(session, job.id)
            assert stored is not None
            assert stored.job_status is RenderJobStatus.COMPLETED
            assert stored.output_checksum == VIDEO
    finally:
        engine.dispose()


def test_stale_render_scan_is_bounded_ordered_and_read_only() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        old = RenderJob.create("package_scan_old", "preview_360p_v1",
                               input_manifest_hash=SHA, now=NOW)
        recent = RenderJob.create("package_scan_recent", "preview_360p_v1",
                                  input_manifest_hash=SHA, now=NOW)
        queued = RenderJob.create("package_scan_queued", "preview_360p_v1",
                                  input_manifest_hash=SHA, now=NOW)
        completed = RenderJob.create("package_scan_complete", "preview_360p_v1",
                                     input_manifest_hash=SHA, now=NOW)
        with media_session(engine) as session:
            for job in (old, recent, queued, completed):
                assert queue_render_job(session, job)
            assert claim_queued_render_job(
                session, job_id=old.id, worker_id="worker-old",
                input_manifest_hash=SHA, started_at=NOW)
            assert claim_queued_render_job(
                session, job_id=recent.id, worker_id="worker-recent",
                input_manifest_hash=SHA, started_at=NOW + timedelta(minutes=2))
            assert claim_queued_render_job(
                session, job_id=completed.id, worker_id="worker-complete",
                input_manifest_hash=SHA, started_at=NOW)
            assert finish_claimed_render_job(
                session, job_id=completed.id, worker_id="worker-complete",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=NOW + timedelta(seconds=5),
                status=RenderJobStatus.COMPLETED, output_checksum=VIDEO)
            session.commit()
        with media_session(engine) as session:
            assert list_stale_render_job_candidates(
                session, started_before=NOW - timedelta(seconds=1)) == []
            assert list_stale_render_job_candidates(
                session, started_before=NOW + timedelta(minutes=1)) == [old.id]
            assert list_stale_render_job_candidates(
                session, started_before=NOW + timedelta(minutes=3)) == [
                    old.id, recent.id,
                ]
            assert list_stale_render_job_candidates(
                session, started_before=NOW + timedelta(minutes=3), limit=1
            ) == [old.id]
            for invalid_limit in (0, -1, 501, True):
                with pytest.raises(ValueError, match="limit"):
                    list_stale_render_job_candidates(
                        session, started_before=NOW, limit=invalid_limit)
            with pytest.raises(ValueError, match="timezone"):
                list_stale_render_job_candidates(
                    session, started_before=NOW.replace(tzinfo=None))
            # Reading candidates cannot change their state or worker owner.
            stored = get_render_job(session, old.id)
            assert stored is not None
            assert stored.job_status is RenderJobStatus.RUNNING
            assert stored.worker_id == "worker-old"
            assert attest_termination(session, old.id, worker_id="worker-old",
                                      started_at=NOW, sha=SHA, at=NOW + timedelta(minutes=5))
            assert quarantine_stale_render_job(
                session, job_id=old.id, input_manifest_hash=SHA,
                started_before=NOW, quarantined_at=NOW + timedelta(minutes=5),
                expected_worker_id="worker-old", expected_started_at=NOW)
            assert list_stale_render_job_candidates(
                session, started_before=NOW + timedelta(minutes=3)
            ) == [recent.id]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("status", "checksum", "code"),
    [(RenderJobStatus.QUEUED, None, None),
     (RenderJobStatus.RUNNING, None, None),
     (RenderJobStatus.COMPLETED, None, None),
     (RenderJobStatus.COMPLETED, "invalid", None),
     (RenderJobStatus.FAILED, None, None),
     (RenderJobStatus.QUARANTINED, None, None)],
)
def test_render_finish_rejects_invalid_terminal_evidence(
    status: RenderJobStatus, checksum: str | None, code: str | None,
) -> None:
    engine = create_media_engine(static_pool=True)
    try:
        job = RenderJob.create("package_01", "preview_360p_v1",
                               input_manifest_hash=SHA, now=NOW)
        with media_session(engine) as session:
            insert_render_job(session, job)
            assert claim_queued_render_job(
                session, job_id=job.id, worker_id="first",
                input_manifest_hash=SHA, started_at=NOW)
            with pytest.raises(ValueError):
                finish_claimed_render_job(
                    session, job_id=job.id, worker_id="first",
                    input_manifest_hash=SHA, started_at=NOW,
                    finished_at=NOW + timedelta(seconds=1),
                    status=status, output_checksum=checksum, error_code=code)
            stored = get_render_job(session, job.id)
            assert stored is not None and stored.job_status is RenderJobStatus.RUNNING
    finally:
        engine.dispose()

"""B02 PostgreSQL render fencing checks on an explicitly isolated temporary DB.

Requires PLM_RENDER_PG_ISOLATED=1 and a PostgreSQL role allowed to create a
disposable database. This never modifies the configured application database.
It does not simulate an FFmpeg crash, artifact reconciliation or a live worker.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from media_factory.api.app import create_app
from media_factory.config import MediaFactorySettings
from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import RenderJob
from media_factory.storage.models import MEDIA_METADATA, render_recovery_evidence_table
from media_factory.storage.render_claims import (
    claim_queued_render_job,
    finish_claimed_render_job,
    list_stale_render_job_candidates,
    quarantine_stale_render_job,
    queue_reconciled_render_retry,
    queue_render_job,
)
from media_factory.storage.repositories import get_render_job
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.db import database_url
from sqlalchemy.orm import Session

from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
    synth_specs,
)
from tests.support.render_evidence import attest_inventory, attest_termination

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
SHA = "a" * 64
VIDEO = "b" * 64


@pytest.fixture(scope="module")
def isolated_render_db() -> Iterator[sa.Engine]:
    if os.getenv("PLM_RENDER_PG_ISOLATED") != "1":
        pytest.skip("PLM_RENDER_PG_ISOLATED=1 required for disposable PostgreSQL DB")
    url = database_url(load_settings(dotenv_path=Path(".env")))
    if url.get_backend_name() != "postgresql":
        pytest.skip("requires PostgreSQL")
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("requires a loopback PostgreSQL endpoint for disposable DB")
    root = sa.create_engine(
        url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5},
    )
    db_name = f"plm_render_claim_probe_{uuid4().hex[:16]}"
    quoted_name = f'"{db_name}"'  # only generated [a-z0-9_]
    created = False
    probe: sa.Engine | None = None
    try:
        with root.connect() as conn:
            conn.exec_driver_sql(f"CREATE DATABASE {quoted_name}")
        created = True
        probe = sa.create_engine(
            url.set(database=db_name), connect_args={"connect_timeout": 5},
        )
        with probe.begin() as conn:
            conn.exec_driver_sql("CREATE SCHEMA media")
        MEDIA_METADATA.create_all(probe)
        yield probe
    finally:
        if probe is not None:
            probe.dispose()
        if created:
            with root.connect() as conn:
                conn.exec_driver_sql(f"DROP DATABASE {quoted_name} WITH (FORCE)")
        root.dispose()


def _job(suffix: str) -> RenderJob:
    return RenderJob.create(
        f"pg_render_{suffix}", "preview_360p_v1",
        input_manifest_hash=SHA, now=NOW,
    )


def test_render_intent_obeys_postgres_outer_rollback(
    isolated_render_db: sa.Engine,
) -> None:
    job = _job("rollback")
    with Session(isolated_render_db) as session:
        assert queue_render_job(session, job)
        session.rollback()
    with Session(isolated_render_db) as session:
        assert get_render_job(session, job.id) is None
        assert queue_render_job(session, job)
        session.commit()
    with Session(isolated_render_db) as session:
        assert not queue_render_job(session, job)
        with pytest.raises(ValueError, match="conflict"):
            queue_render_job(
                session, replace(job, input_manifest_hash="f" * 64))


def test_concurrent_queue_replays_are_one_postgres_intent(
    isolated_render_db: sa.Engine,
) -> None:
    job = _job("queue_race")
    barrier = Barrier(2)

    def queue() -> bool:
        with Session(isolated_render_db) as session:
            barrier.wait(timeout=10)
            inserted = queue_render_job(session, job)
            session.commit()
            return inserted

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(queue)
        b = pool.submit(queue)
        outcomes = [a.result(timeout=20), b.result(timeout=20)]
    assert sorted(outcomes) == [False, True]
    with Session(isolated_render_db) as session:
        stored = get_render_job(session, job.id)
        assert stored is not None
        assert stored.job_status is RenderJobStatus.QUEUED
        assert stored.input_manifest_hash == SHA


def test_two_workers_cannot_both_claim_one_render(
    isolated_render_db: sa.Engine,
) -> None:
    job = _job("competition")
    with Session(isolated_render_db) as session:
        assert queue_render_job(session, job)
        session.commit()

    barrier = Barrier(2)

    def claim(worker: str) -> tuple[str, bool]:
        with Session(isolated_render_db) as session:
            barrier.wait(timeout=10)
            won = claim_queued_render_job(
                session, job_id=job.id, worker_id=worker,
                input_manifest_hash=SHA, started_at=NOW)
            session.commit()
            return worker, won

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(claim, "worker-a")
        b = pool.submit(claim, "worker-b")
        results = [a.result(timeout=20), b.result(timeout=20)]
    assert sum(int(won) for _, won in results) == 1
    winner = next(worker for worker, won in results if won)
    loser = next(worker for worker, won in results if not won)
    with Session(isolated_render_db) as session:
        stored = get_render_job(session, job.id)
        assert stored is not None
        assert stored.job_status is RenderJobStatus.RUNNING
        assert stored.worker_id == winner
        assert not finish_claimed_render_job(
            session, job_id=job.id, worker_id=loser,
            input_manifest_hash=SHA, started_at=NOW,
            finished_at=NOW + timedelta(minutes=1),
            status=RenderJobStatus.COMPLETED, output_checksum=VIDEO)
        # A bounded scan discovers the old claim but does not claim it lost.
        assert list_stale_render_job_candidates(
            session, started_before=NOW - timedelta(seconds=1)
        ) == []
        assert list_stale_render_job_candidates(
            session, started_before=NOW + timedelta(minutes=1), limit=1
        ) == [job.id]
        assert not quarantine_stale_render_job(
            session, job_id=job.id, input_manifest_hash=SHA,
            started_before=NOW, quarantined_at=NOW + timedelta(minutes=5),
            expected_worker_id=winner, expected_started_at=NOW)
        assert attest_termination(session, job.id, worker_id=winner, started_at=NOW,
                                  sha=SHA, at=NOW + timedelta(minutes=5))
        assert quarantine_stale_render_job(
            session, job_id=job.id, input_manifest_hash=SHA,
            started_before=NOW, quarantined_at=NOW + timedelta(minutes=5),
            expected_worker_id=winner, expected_started_at=NOW)
        assert list_stale_render_job_candidates(
            session, started_before=NOW + timedelta(minutes=10)) == []


def test_postgres_completion_and_stale_quarantine_are_mutually_exclusive(
    isolated_render_db: sa.Engine,
) -> None:
    job = _job("terminal_race")
    with Session(isolated_render_db) as session:
        assert queue_render_job(session, job)
        session.commit()
    with Session(isolated_render_db) as session:
        assert claim_queued_render_job(
            session, job_id=job.id, worker_id="racer",
            input_manifest_hash=SHA, started_at=NOW)
        assert attest_termination(session, job.id, worker_id="racer", started_at=NOW,
                                  sha=SHA, at=NOW + timedelta(minutes=2))
        session.commit()

    barrier = Barrier(2)

    def finish() -> bool:
        with Session(isolated_render_db) as session:
            barrier.wait(timeout=10)
            won = finish_claimed_render_job(
                session, job_id=job.id, worker_id="racer",
                input_manifest_hash=SHA, started_at=NOW,
                finished_at=NOW + timedelta(minutes=2),
                status=RenderJobStatus.COMPLETED, output_checksum=VIDEO)
            session.commit()
            return won

    def quarantine() -> bool:
        with Session(isolated_render_db) as session:
            barrier.wait(timeout=10)
            won = quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA,
                started_before=NOW, quarantined_at=NOW + timedelta(minutes=2),
                expected_worker_id="racer", expected_started_at=NOW)
            session.commit()
            return won

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(finish)
        b = pool.submit(quarantine)
        outcomes = [a.result(timeout=20), b.result(timeout=20)]
    assert sum(int(won) for won in outcomes) == 1
    with Session(isolated_render_db) as session:
        stored = get_render_job(session, job.id)
        assert stored is not None
        if outcomes[0]:
            assert stored.job_status is RenderJobStatus.COMPLETED
            assert stored.output_checksum == VIDEO
        else:
            assert stored.job_status is RenderJobStatus.QUARANTINED
            assert stored.error_code == "render_run_stale"
            assert stored.output_checksum is None


def test_http_assembly_commits_fenced_render_and_package_on_postgres(
    isolated_render_db: sa.Engine,
) -> None:
    """Actual API/DB sessions commit intent before fake render and package.

    StandInToolchain writes placeholders, not FFmpeg video; this proves
    transaction ordering and replay using PostgreSQL, not media validity.
    """
    store = InMemoryArtifactStore()
    manifests = [
        stored_manifest(
            store, f"pg_http_{i}", measured_duration_seconds=60.0, synth=spec,
        )
        for i, spec in enumerate(synth_specs())
    ]
    toolchain = StandInToolchain()
    platform = load_settings(env={
        "DATABASE_URL": "postgresql://dummy:dummy@127.0.0.1:9/unreachable",
        "POSTGRES_PASSWORD": "unused",
        "MINIO_ENDPOINT": "127.0.0.1:9",
        "MINIO_ROOT_USER": "unused",
        "MINIO_ROOT_PASSWORD": "unused",
        "MINIO_BUCKET": "unused",
        "TEMPORAL_HOST": "127.0.0.1:9",
        "PLM_SERVICE_TOKEN": "pg-http-test-token",
    })
    client = TestClient(create_app(
        session_factory=lambda: Session(isolated_render_db),
        settings=MediaFactorySettings(platform=platform), clock=FixedClock(NOW),
        backend=backend_for(store, manifests, toolchain=toolchain),
    ))
    body = {
        "candidate_ids": [m.candidate_id for m in manifests],
        "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "brand_neon_rain_v1",
        "playlist_concept": "Deep Focus",
        "render_profile": "preview_360p_v1",
    }
    headers = {SERVICE_TOKEN_HEADER: "pg-http-test-token"}
    response = client.post("/media/packages/assemble", json=body, headers=headers)
    assert response.status_code == 200, response.text
    package = response.json()
    job_id = package["render_report"]["render_job_id"]
    with Session(isolated_render_db) as session:
        stored = get_render_job(session, job_id)
        assert stored is not None
        assert stored.job_status is RenderJobStatus.COMPLETED
        assert stored.worker_id
        assert stored.input_manifest_hash
        assert stored.output_checksum == package["video_checksum"]
    before_replay = list(toolchain.calls)
    replay = client.post("/media/packages/assemble", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["media_package_id"] == package["media_package_id"]
    assert toolchain.calls == before_replay


def _quarantined_with_evidence(engine: sa.Engine, suffix: str) -> RenderJob:
    job = _job(suffix)
    with Session(engine) as session:
        assert queue_render_job(session, job)
        assert claim_queued_render_job(
            session, job_id=job.id, worker_id="lost", input_manifest_hash=SHA, started_at=NOW)
        assert attest_termination(session, job.id, worker_id="lost", started_at=NOW,
                                  sha=SHA, at=NOW + timedelta(minutes=1))
        assert quarantine_stale_render_job(
            session, job_id=job.id, input_manifest_hash=SHA, started_before=NOW,
            quarantined_at=NOW + timedelta(minutes=1), expected_worker_id="lost",
            expected_started_at=NOW)
        assert attest_inventory(session, job.id, worker_id="lost", started_at=NOW,
                                sha=SHA, at=NOW + timedelta(minutes=2))
        session.commit()
    return job


def test_concurrent_evidence_records_are_one_postgres_row(
    isolated_render_db: sa.Engine,
) -> None:
    job = _job("evidence_race")
    with Session(isolated_render_db) as session:
        assert queue_render_job(session, job)
        assert claim_queued_render_job(
            session, job_id=job.id, worker_id="lost", input_manifest_hash=SHA, started_at=NOW)
        session.commit()
    barrier = Barrier(2)

    def record() -> bool:
        with Session(isolated_render_db) as session:
            barrier.wait(timeout=10)
            inserted = attest_termination(session, job.id, worker_id="lost",
                                          started_at=NOW, sha=SHA, at=NOW)
            session.commit()
            return inserted

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [f.result(timeout=20) for f in (pool.submit(record), pool.submit(record))]
    assert sorted(outcomes) == [False, True]
    with Session(isolated_render_db) as session:
        rows = [row for row in session.execute(
            sa.select(render_recovery_evidence_table)).mappings().all()
            if row["render_job_id"] == job.id]
        assert len(rows) == 1
        assert rows[0]["detail_json"] == {"simulated": True}
        assert rows[0]["started_at"] == NOW
        with pytest.raises(ValueError, match="does not match"):
            attest_termination(session, _job("never_claimed").id, worker_id="lost",
                               started_at=NOW, sha=SHA, at=NOW)


def test_quarantine_without_evidence_is_refused_on_postgres(
    isolated_render_db: sa.Engine,
) -> None:
    job = _job("no_evidence")
    with Session(isolated_render_db) as session:
        assert queue_render_job(session, job)
        assert claim_queued_render_job(
            session, job_id=job.id, worker_id="lost", input_manifest_hash=SHA, started_at=NOW)
        assert not quarantine_stale_render_job(
            session, job_id=job.id, input_manifest_hash=SHA, started_before=NOW,
            quarantined_at=NOW + timedelta(hours=1), expected_worker_id="lost",
            expected_started_at=NOW)
        session.commit()
        stored = get_render_job(session, job.id)
        assert stored is not None and stored.job_status is RenderJobStatus.RUNNING


def test_two_supervisors_stage_exactly_one_postgres_retry(
    isolated_render_db: sa.Engine,
) -> None:
    prior = _quarantined_with_evidence(isolated_render_db, "retry_race")
    barrier = Barrier(2)

    def stage() -> RenderJob | None:
        with Session(isolated_render_db) as session:
            barrier.wait(timeout=10)
            staged = queue_reconciled_render_retry(
                session, previous_job_id=prior.id, expected_worker_id="lost",
                expected_started_at=NOW, input_manifest_hash=SHA,
                now=NOW + timedelta(minutes=3))
            session.commit()
            return staged

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [f.result(timeout=20) for f in (pool.submit(stage), pool.submit(stage))]
    staged = [item for item in outcomes if item is not None]
    assert len(staged) == 1
    with Session(isolated_render_db) as session:
        retry = get_render_job(session, staged[0].id)
        # Name-keyed INSERT ... SELECT: every column lands where it belongs.
        assert retry is not None
        assert retry.job_status is RenderJobStatus.QUEUED
        assert retry.attempt_count == 2
        assert retry.media_package_id == prior.media_package_id
        assert retry.render_profile == prior.render_profile
        assert retry.render_version == prior.render_version
        assert retry.priority == prior.priority
        assert retry.input_manifest_hash == SHA
        assert retry.worker_id is None and retry.started_at is None
        assert retry.finished_at is None and retry.output_checksum is None
        assert retry.error_code is None and retry.error_detail is None
        assert retry.created_at == NOW + timedelta(minutes=3)


def test_old_attempt_cannot_finish_once_postgres_retry_is_claimed(
    isolated_render_db: sa.Engine,
) -> None:
    prior = _quarantined_with_evidence(isolated_render_db, "late_finish")
    later = NOW + timedelta(minutes=4)
    with Session(isolated_render_db) as session:
        retry = queue_reconciled_render_retry(
            session, previous_job_id=prior.id, expected_worker_id="lost",
            expected_started_at=NOW, input_manifest_hash=SHA,
            now=NOW + timedelta(minutes=3))
        assert retry is not None
        assert claim_queued_render_job(
            session, job_id=retry.id, worker_id="fresh", input_manifest_hash=SHA,
            started_at=later)
        session.commit()
    barrier = Barrier(2)

    def finish(job_id: str, worker: str, started: datetime) -> bool:
        with Session(isolated_render_db) as session:
            barrier.wait(timeout=10)
            won = finish_claimed_render_job(
                session, job_id=job_id, worker_id=worker, input_manifest_hash=SHA,
                started_at=started, finished_at=later + timedelta(minutes=1),
                status=RenderJobStatus.COMPLETED, output_checksum=VIDEO)
            session.commit()
            return won

    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(finish, prior.id, "lost", NOW)
        new = pool.submit(finish, retry.id, "fresh", later)
        assert (old.result(timeout=20), new.result(timeout=20)) == (False, True)
    with Session(isolated_render_db) as session:
        old_job = get_render_job(session, prior.id)
        new_job = get_render_job(session, retry.id)
        assert old_job is not None and old_job.job_status is RenderJobStatus.QUARANTINED
        assert new_job is not None and new_job.job_status is RenderJobStatus.COMPLETED


def test_http_render_failure_fences_its_own_claim_on_postgres(
    isolated_render_db: sa.Engine,
) -> None:
    """Owner-observed exit through the real API and PostgreSQL sessions.

    The renderer raises in-process (stand-in); this is not a killed process.
    A separate brand keeps P6's cross-album near-copy check from refusing this
    album against the earlier test's identical synthetic audio.
    """
    from unittest.mock import patch

    from media_factory.brands.registry import BrandRegistry
    from media_factory.visuals.planner import BrandProfile

    store = InMemoryArtifactStore()
    manifests = [
        stored_manifest(store, f"pg_exit_{i}", measured_duration_seconds=60.0, synth=spec)
        for i, spec in enumerate(synth_specs())
    ]
    platform = load_settings(env={
        "DATABASE_URL": "postgresql://dummy:dummy@127.0.0.1:9/unreachable",
        "POSTGRES_PASSWORD": "unused", "MINIO_ENDPOINT": "127.0.0.1:9",
        "MINIO_ROOT_USER": "unused", "MINIO_ROOT_PASSWORD": "unused",
        "MINIO_BUCKET": "unused", "TEMPORAL_HOST": "127.0.0.1:9",
        "PLM_SERVICE_TOKEN": "pg-http-test-token",
    })
    client = TestClient(create_app(
        session_factory=lambda: Session(isolated_render_db),
        settings=MediaFactorySettings(platform=platform), clock=FixedClock(NOW),
        backend=backend_for(store, manifests, toolchain=StandInToolchain(), brands=BrandRegistry(
            [BrandProfile(brand_id="brand_exit", brand_name="Exit Records")])),
    ))
    body = {
        "candidate_ids": [m.candidate_id for m in manifests],
        "brand_id": "brand_exit",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "brand_neon_rain_v1",
        "playlist_concept": "Owner Exit",
        "render_profile": "preview_360p_v1",
    }
    headers = {SERVICE_TOKEN_HEADER: "pg-http-test-token"}
    with (
        patch("media_factory.rendering.assembler.MediaRenderer.render",
              side_effect=RuntimeError("encoder crashed")),
        pytest.raises(RuntimeError, match="encoder crashed"),
    ):
        client.post("/media/packages/assemble", json=body, headers=headers)
    with Session(isolated_render_db) as session:
        evidence = session.execute(
            sa.select(render_recovery_evidence_table)
            .where(render_recovery_evidence_table.c.method == "owner_exit_observed")
        ).mappings().all()
        assert len(evidence) == 1
        job = get_render_job(session, evidence[0]["render_job_id"])
        assert job is not None
        assert job.job_status is RenderJobStatus.QUARANTINED
        assert job.started_at == NOW and evidence[0]["started_at"] == NOW
        assert evidence[0]["worker_id"] == job.worker_id
    replay = client.post("/media/packages/assemble", json=body, headers=headers)
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "render_job_reconciliation_required"


def test_failed_package_insert_after_completion_fences_without_deadlock_on_postgres(
    isolated_render_db: sa.Engine,
) -> None:
    """The completion UPDATE holds the job row; fencing must not wait on it forever."""
    from unittest.mock import patch

    from media_factory.brands.registry import BrandRegistry
    from media_factory.visuals.planner import BrandProfile

    store = InMemoryArtifactStore()
    manifests = [
        stored_manifest(store, f"pg_persist_{i}", measured_duration_seconds=60.0, synth=spec)
        for i, spec in enumerate(synth_specs())
    ]
    platform = load_settings(env={
        "DATABASE_URL": "postgresql://dummy:dummy@127.0.0.1:9/unreachable",
        "POSTGRES_PASSWORD": "unused", "MINIO_ENDPOINT": "127.0.0.1:9",
        "MINIO_ROOT_USER": "unused", "MINIO_ROOT_PASSWORD": "unused",
        "MINIO_BUCKET": "unused", "TEMPORAL_HOST": "127.0.0.1:9",
        "PLM_SERVICE_TOKEN": "pg-http-test-token",
    })
    client = TestClient(create_app(
        session_factory=lambda: Session(isolated_render_db),
        settings=MediaFactorySettings(platform=platform), clock=FixedClock(NOW),
        backend=backend_for(store, manifests, toolchain=StandInToolchain(), brands=BrandRegistry(
            [BrandProfile(brand_id="brand_persist", brand_name="Persist Records")])),
    ))
    body = {
        "candidate_ids": [m.candidate_id for m in manifests],
        "brand_id": "brand_persist",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "brand_neon_rain_v1",
        "playlist_concept": "Persist Failure",
        "render_profile": "preview_360p_v1",
    }
    with (
        patch("media_factory.service.insert_media_package",
              side_effect=RuntimeError("package insert failed")),
        pytest.raises(RuntimeError, match="package insert failed"),
    ):
        client.post("/media/packages/assemble", json=body,
                    headers={SERVICE_TOKEN_HEADER: "pg-http-test-token"})
    with Session(isolated_render_db) as session:
        rows = session.execute(
            sa.select(render_recovery_evidence_table.c.render_job_id)
            .where(render_recovery_evidence_table.c.detail_json["exception_type"].as_string()
                   == "RuntimeError",
                   render_recovery_evidence_table.c.method == "owner_exit_observed")
        ).scalars().all()
        jobs = [get_render_job(session, job_id) for job_id in rows]
        persisted = [j for j in jobs if j is not None and j.media_package_id.startswith("mpkg")]
        assert any(j.job_status is RenderJobStatus.QUARANTINED and j.output_checksum is None
                   for j in persisted)

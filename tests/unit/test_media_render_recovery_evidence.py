"""B02 persisted recovery evidence (ADR-0043), SQLite only.

Proves the SQL gates and the owner-exit path with a stand-in toolchain. It does
not prove a real FFmpeg process stopped, nor inventory a real object store.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from media_factory.api.schemas import AssemblePackageRequest
from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import RenderJob
from media_factory.rendering.profiles import RENDER_PROFILES
from media_factory.service import assemble_and_persist
from media_factory.storage.models import render_jobs_table, render_recovery_evidence_table
from media_factory.storage.render_claims import (
    RecoveryEvidenceKind,
    claim_queued_render_job,
    quarantine_stale_render_job,
    queue_render_job,
    record_render_recovery_evidence,
)
from media_factory.storage.repositories import get_render_job
from plm_platform.clock import FixedClock
from sqlalchemy.orm import Session

from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
    synth_specs,
)
from tests.support.media_db import create_media_engine, media_session
from tests.support.render_evidence import attest_inventory, attest_termination

NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)
SHA = "a" * 64


def _claimed(session: Session, package: str, worker: str = "owner") -> RenderJob:
    job = RenderJob.create(package, "preview_360p_v1", input_manifest_hash=SHA, now=NOW)
    assert queue_render_job(session, job)
    assert claim_queued_render_job(
        session, job_id=job.id, worker_id=worker, input_manifest_hash=SHA, started_at=NOW)
    return job


def _record(session: Session, job_id: str, **overrides: Any) -> bool:
    arguments: dict[str, Any] = {
        "job_id": job_id, "kind": RecoveryEvidenceKind.WORKER_TERMINATION,
        "method": "supervisor_attestation", "worker_id": "owner", "started_at": NOW,
        "input_manifest_hash": SHA, "recorded_by": "supervisor", "recorded_at": NOW,
        "detail": {"pid_absent": True}, "evidence_ref": "ticket://1",
    }
    arguments.update(overrides)
    return record_render_recovery_evidence(session, **arguments)


@pytest.mark.parametrize("override", [
    {"worker_id": "someone-else"},
    {"started_at": NOW + timedelta(microseconds=1)},
    {"input_manifest_hash": "b" * 64},
    {"job_id": "rjob_missing"},
])
def test_evidence_binds_to_the_exact_claimed_execution(override: dict[str, Any]) -> None:
    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            job = _claimed(session, "bind")
            with pytest.raises(ValueError, match="does not match a claimed execution"):
                _record(session, override.pop("job_id", job.id), **override)
            assert session.execute(
                sa.select(sa.func.count()).select_from(render_recovery_evidence_table)
            ).scalar_one() == 0
    finally:
        engine.dispose()


def test_unclaimed_job_and_running_inventory_cannot_be_attested() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            queued = RenderJob.create("unclaimed", "preview_360p_v1",
                                      input_manifest_hash=SHA, now=NOW)
            assert queue_render_job(session, queued)
            with pytest.raises(ValueError, match="does not match"):
                _record(session, queued.id)
            running = _claimed(session, "running")
            # Outputs of a live claim are not orphans: inventory needs QUARANTINED.
            with pytest.raises(ValueError, match="does not match"):
                attest_inventory(session, running.id, worker_id="owner",
                                 started_at=NOW, sha=SHA, at=NOW)
    finally:
        engine.dispose()


@pytest.mark.parametrize("override, message", [
    ({"method": "claim_namespace_inventory"}, "cannot attest"),
    ({"method": "age_exceeded"}, "cannot attest"),
    ({"evidence_ref": None}, "requires an evidence reference"),
    ({"evidence_ref": "   "}, "requires an evidence reference"),
    ({"detail": {"value": float("nan")}}, "strict JSON"),
    ({"detail": {"value": object()}}, "strict JSON"),
    ({"started_at": NOW.replace(tzinfo=None)}, "timezone"),
    ({"recorded_by": ""}, "must not be empty"),
    ({"input_manifest_hash": "short"}, "SHA256"),
])
def test_evidence_shape_is_validated(override: dict[str, Any], message: str) -> None:
    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            job = _claimed(session, "shape")
            with pytest.raises(ValueError, match=message):
                _record(session, job.id, **override)
    finally:
        engine.dispose()


@pytest.mark.parametrize("detail", [
    {"object_count": 0, "truncated": True},
    {"object_count": 0},
    {"object_count": True, "truncated": False},
    {"object_count": -1, "truncated": False},
    {"truncated": False},
])
def test_truncated_or_uncounted_listing_is_not_an_inventory(detail: dict[str, Any]) -> None:
    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            job = _claimed(session, "inventory")
            with pytest.raises(ValueError, match="complete, counted listing"):
                _record(session, job.id, kind=RecoveryEvidenceKind.ORPHAN_ARTIFACTS,
                        method="claim_namespace_inventory", detail=detail)
    finally:
        engine.dispose()


def test_replay_is_idempotent_and_a_different_second_record_conflicts() -> None:
    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            job = _claimed(session, "replay")
            assert _record(session, job.id)
            assert not _record(session, job.id)
            with pytest.raises(ValueError, match="already recorded differently"):
                _record(session, job.id, evidence_ref="ticket://2")
            with pytest.raises(ValueError, match="already recorded differently"):
                _record(session, job.id, method="owner_exit_observed", evidence_ref=None)
            session.commit()
        with media_session(engine) as session:
            assert session.execute(
                sa.select(sa.func.count()).select_from(render_recovery_evidence_table)
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_evidence_obeys_the_outer_transaction() -> None:
    """Evidence and the transition it supports commit or vanish together."""
    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            job = _claimed(session, "rollback")
            session.commit()
        with media_session(engine) as session:
            assert attest_termination(session, job.id, worker_id="owner",
                                      started_at=NOW, sha=SHA, at=NOW)
            assert quarantine_stale_render_job(
                session, job_id=job.id, input_manifest_hash=SHA, started_before=NOW,
                quarantined_at=NOW, expected_worker_id="owner", expected_started_at=NOW)
            session.rollback()
        with media_session(engine) as session:
            assert get_render_job(session, job.id).job_status is RenderJobStatus.RUNNING
            assert session.execute(
                sa.select(sa.func.count()).select_from(render_recovery_evidence_table)
            ).scalar_one() == 0
    finally:
        engine.dispose()


def _assembly() -> tuple[Any, AssemblePackageRequest, StandInToolchain]:
    store, toolchain = InMemoryArtifactStore(), StandInToolchain()
    manifests = [
        stored_manifest(store, f"evidence_track_{n}", measured_duration_seconds=60, synth=spec)
        for n, spec in enumerate(synth_specs())
    ]
    request = AssemblePackageRequest.model_validate({
        "candidate_ids": [item.candidate_id for item in manifests],
        "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "neon_waveform", "playlist_concept": "Deep Focus",
        "render_profile": "preview_360p_v1",
    })
    return backend_for(store, manifests, toolchain=toolchain), request, toolchain


def _assemble(engine: sa.Engine, factory: Callable[[], Session]) -> None:
    backend, request, _ = _assembly()
    with media_session(engine) as session:
        assemble_and_persist(
            session, backend, request, clock=FixedClock(NOW),
            default_render_profile=RENDER_PROFILES["preview_360p_v1"].name,
            render_session_factory=factory, worker_id_factory=lambda: "owner")


def _only_job(engine: sa.Engine) -> dict[str, Any]:
    with media_session(engine) as session:
        return dict(session.execute(sa.select(render_jobs_table)).mappings().one())


def test_a_failed_owner_fence_never_masks_the_render_error() -> None:
    engine = create_media_engine(static_pool=True)
    opened = 0

    def flaky_factory() -> Session:
        nonlocal opened
        opened += 1
        if opened > 2:  # intent and claim succeed; the fence session cannot open
            raise ConnectionError("database went away")
        return media_session(engine)

    try:
        with (
            patch("media_factory.rendering.assembler.MediaRenderer.render",
                  side_effect=RuntimeError("encoder exploded")),
            pytest.raises(RuntimeError, match="encoder exploded") as raised,
        ):
            _assemble(engine, flaky_factory)
        assert any("left RUNNING" in note for note in raised.value.__notes__)
        assert _only_job(engine)["job_status"] == RenderJobStatus.RUNNING.value
    finally:
        engine.dispose()


def test_an_interrupt_is_not_treated_as_an_observed_owner_exit() -> None:
    """Only ordinary exceptions are owner-observed; a kill-like interrupt stays RUNNING."""
    engine = create_media_engine(static_pool=True)
    try:
        with (
            patch("media_factory.rendering.assembler.MediaRenderer.render",
                  side_effect=KeyboardInterrupt()),
            pytest.raises(KeyboardInterrupt),
        ):
            _assemble(engine, lambda: media_session(engine))
        job = _only_job(engine)
        assert job["job_status"] == RenderJobStatus.RUNNING.value
        with media_session(engine) as session:
            assert session.execute(
                sa.select(sa.func.count()).select_from(render_recovery_evidence_table)
            ).scalar_one() == 0
    finally:
        engine.dispose()


def test_owner_exit_evidence_names_its_claim_and_is_not_overwritten() -> None:
    """The owner's record is bound to its claim; a supervisor cannot replace it."""
    engine = create_media_engine(static_pool=True)
    try:
        with (
            patch("media_factory.rendering.assembler.MediaRenderer.render",
                  side_effect=RuntimeError("first attempt failed")),
            pytest.raises(RuntimeError),
        ):
            _assemble(engine, lambda: media_session(engine))
        first = _only_job(engine)
        assert first["job_status"] == RenderJobStatus.QUARANTINED.value
        with media_session(engine) as session:
            evidence = session.execute(
                sa.select(render_recovery_evidence_table)).mappings().one()
            assert evidence["render_job_id"] == first["id"]
            assert evidence["recorded_by"] == "owner"
            assert evidence["detail_json"]["exception_type"] == "RuntimeError"
            # One record per claim and kind: no second, different record.
            with pytest.raises(ValueError, match="already recorded differently"):
                attest_termination(session, first["id"], worker_id="owner",
                                   started_at=NOW, sha=first["input_manifest_hash"], at=NOW)
    finally:
        engine.dispose()


def test_a_finished_render_that_cannot_be_persisted_is_fenced() -> None:
    """Failures after the render (persisting its records) also fence the claim."""
    engine = create_media_engine(static_pool=True)
    try:
        with (
            patch("media_factory.service.insert_media_package",
                  side_effect=RuntimeError("package insert failed")),
            pytest.raises(RuntimeError, match="package insert failed"),
        ):
            _assemble(engine, lambda: media_session(engine))
        job = _only_job(engine)
        assert job["job_status"] == RenderJobStatus.QUARANTINED.value
        with media_session(engine) as session:
            evidence = session.execute(
                sa.select(render_recovery_evidence_table)).mappings().one()
            assert evidence["method"] == "owner_exit_observed"
    finally:
        engine.dispose()

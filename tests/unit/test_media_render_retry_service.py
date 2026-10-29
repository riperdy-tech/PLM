"""B02 staged retry execution through real P6 assembly and SQLite, with fake toolchain.

Not a supervisor, termination proof, real encoder, or production recovery gate.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from media_factory.api.schemas import AssemblePackageRequest
from media_factory.domain.enums import RenderJobStatus
from media_factory.errors import MediaAssemblyError, MediaInfrastructureError
from media_factory.rendering.profiles import RENDER_PROFILES
from media_factory.rendering.toolchain import MediaToolError, VisualizerSpec
from media_factory.service import assemble_and_persist
from media_factory.storage.models import render_jobs_table
from media_factory.storage.render_claims import (
    quarantine_stale_render_job,
    queue_reconciled_render_retry,
)
from media_factory.storage.repositories import get_render_job
from plm_platform.clock import FixedClock

from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
    synth_specs,
)
from tests.support.media_db import create_media_engine, media_session

NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)


class OneCrashToolchain(StandInToolchain):
    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    def render_video_master(self, audio_master: Path, visual: VisualizerSpec,
                            profile: object, out: Path) -> None:
        if self.fail_once:
            self.fail_once = False
            self.calls.append("video_crashed")
            raise MediaToolError("render_timeout", "simulated worker exit")
        super().render_video_master(audio_master, visual, profile, out)  # type: ignore[arg-type]


def test_prepared_retry_uses_second_attempt_and_refuses_changed_inputs() -> None:
    engine = create_media_engine(static_pool=True)
    store, toolchain = InMemoryArtifactStore(), OneCrashToolchain()
    manifests = [
        stored_manifest(store, f"retry_track_{n}", measured_duration_seconds=60,
                        synth=spec)
        for n, spec in enumerate(synth_specs())
    ]
    backend = backend_for(store, manifests, toolchain=toolchain)
    request = AssemblePackageRequest.model_validate({
        "candidate_ids": [item.candidate_id for item in manifests],
        "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "neon_waveform", "playlist_concept": "Deep Focus",
        "render_profile": "preview_360p_v1",
    })
    factory = lambda: media_session(engine)
    try:
        with factory() as session:
            with pytest.raises(MediaInfrastructureError, match="render_timeout"):
                assemble_and_persist(
                    session, backend, request, clock=FixedClock(NOW),
                    default_render_profile=RENDER_PROFILES["preview_360p_v1"].name,
                    render_session_factory=factory,
                    worker_id_factory=lambda: "terminated-worker",
                )
            session.rollback()
        with factory() as session:
            initial_id = session.execute(sa.select(render_jobs_table.c.id)).scalar_one()
            initial = get_render_job(session, initial_id)
            assert initial is not None
            assert initial.job_status is RenderJobStatus.RUNNING
            assert initial.started_at is not None
            assert initial.worker_id == "terminated-worker"
            session.rollback()
        with factory() as session:
            assert quarantine_stale_render_job(
                session, job_id=initial.id, input_manifest_hash=initial.input_manifest_hash,
                started_before=NOW, quarantined_at=NOW + timedelta(minutes=5),
                expected_worker_id="terminated-worker", expected_started_at=NOW,
                worker_termination_confirmed=True,
            )
            prepared = queue_reconciled_render_retry(
                session, previous_job_id=initial.id,
                expected_worker_id="terminated-worker", expected_started_at=NOW,
                input_manifest_hash=initial.input_manifest_hash,
                worker_termination_confirmed=True, orphan_artifacts_reconciled=True,
                source_inputs_reverified=True, now=NOW + timedelta(minutes=6),
            )
            assert prepared is not None and prepared.attempt_count == 2
            session.commit()
        with factory() as session:
            altered = request.model_copy(update={"playlist_concept": "Changed Mood"})
            with pytest.raises(MediaAssemblyError, match="render_retry_input_changed"):
                assemble_and_persist(
                    session, backend, altered, clock=FixedClock(NOW + timedelta(minutes=8)),
                    default_render_profile="preview_360p_v1", render_session_factory=factory,
                    worker_id_factory=lambda: "new-worker", recovery_job_id=prepared.id,
                )
            session.rollback()
            assert "video" not in toolchain.calls
        with factory() as session:
            package = assemble_and_persist(
                session, backend, request, clock=FixedClock(NOW + timedelta(minutes=10)),
                default_render_profile="preview_360p_v1", render_session_factory=factory,
                worker_id_factory=lambda: "new-worker", recovery_job_id=prepared.id,
            )
            assert package.render_report["render_job_id"] == prepared.id
            assert package.status.value == "ready_for_publish"
            session.commit()
        with factory() as session:
            old = get_render_job(session, initial.id)
            new = get_render_job(session, prepared.id)
            assert old is not None and old.job_status is RenderJobStatus.QUARANTINED
            assert new is not None and new.job_status is RenderJobStatus.COMPLETED
            assert new.worker_id == "new-worker"
        assert toolchain.calls.count("video") == 1
    finally:
        engine.dispose()

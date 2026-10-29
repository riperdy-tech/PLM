"""Real Temporal/ops failure handoff: P6 response -> pipeline HTTP -> FAILED.

P6 runs as an in-process stand-in renderer. P3/P5 HTTP requests are served by a
mock transport and pre-assemble steps are scripted. The workflow, Temporal
server, acceptance-queue worker and PostgreSQL transition store are real.
No deployed pipeline worker, P7 provider, MinIO object or external write is used.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plm_platform.pipeline.mock import MockPhaseSteps, default_scripts
from plm_platform.pipeline.states import PipelineState, Step
from plm_platform.pipeline.types import PipelineOutcome, StepRequest, StepResult
from plm_platform.temporal import idempotency_key
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from tests.integration.pipeline.support import (
    new_run,
    running,
    start_run,
    transitions,
    worker_for,
)
from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
    synth_specs,
)
from tests.unit.test_media_api import HEADERS, _client, _request
from tests.unit.test_pipeline_http_assemble import _Phases, _steps

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _FailedAssemblySteps(MockPhaseSteps):
    """Script only pre-P6 phases; use actual HttpPhaseSteps for assemble."""

    def __init__(self, http_steps: Any) -> None:
        scripts = default_scripts()
        scripts[Step.POLICY_CHECK].outputs["package_ids"] = ["pkg_1"]
        scripts[Step.GENERATE].outputs["candidate_ids"] = ["c0", "c1", "c2"]
        super().__init__(scripts)
        self.http_steps = http_steps

    async def assemble(self, request: StepRequest) -> StepResult:
        self.calls.append((Step.ASSEMBLE.value, request.idempotency_key))
        return await self.http_steps.assemble(request)


@pytest.mark.parametrize(("probe_overrides", "expected_code"), [
    ({"audio_stream_duration_seconds": None}, "stream_duration_unmeasured"),
    ({"audio_stream_duration_seconds": 80.0}, "video_audio_duration_mismatch"),
    ({"video_stream_duration_seconds": None}, "stream_duration_unmeasured"),
])
async def test_p6_timing_failure_ends_real_temporal_run_before_p7(
    client: Client, factory: sessionmaker[Session], platform: Any,
    probe_overrides: dict[str, float | None], expected_code: str,
) -> None:
    store = InMemoryArtifactStore()
    manifests = [
        stored_manifest(store, f"temporal_src_{n}",
                        measured_duration_seconds=60.0, synth=synth)
        for n, synth in enumerate(synth_specs())
    ]
    p6 = _client(backend_for(
        store, manifests, toolchain=StandInToolchain(probe_overrides=probe_overrides),
    ))
    response = p6.post(
        "/media/packages/assemble",
        json=_request(*[manifest.candidate_id for manifest in manifests]),
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    failed_p6 = response.json()
    assert failed_p6["status"] == "failed"
    assert expected_code in failed_p6["blocking_reason_codes"]

    directive_id, workflow_id = new_run()
    phases = _Phases(
        {"c0": (200, failed_p6)}, step_key=idempotency_key(workflow_id, Step.ASSEMBLE.value),
    )
    steps = _FailedAssemblySteps(_steps(phases))
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), 120.0)

    assert (outcome.final_state, outcome.completed) == ("FAILED", False)
    assert outcome.error_code == "ValidationFailure"
    records = transitions(factory, workflow_id)
    failure = records[-1]
    assert failure.to_state is PipelineState.FAILED
    assert failure.from_state is PipelineState.ASSEMBLING
    assert failure.step == Step.ASSEMBLE.value and failure.final
    assert failure.attempt == 1  # non-retryable
    assert failure.error_code == "ValidationFailure"
    assert expected_code in (failure.error_detail or "")
    assert failure.outputs["failure"]["non_retryable"] is True
    assert all(row.to_state not in (PipelineState.PACKAGED, PipelineState.UPLOADING)
               for row in records)
    assert len(phases.p6_bodies) == 1
    assert len(steps.calls_for(Step.ASSEMBLE)) == 1
    assert steps.calls_for(Step.UPLOAD) == []
    assert steps.effects_for(Step.UPLOAD) == 0

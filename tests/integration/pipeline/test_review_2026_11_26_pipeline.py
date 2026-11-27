"""Review 2026-11-26, pipeline core: the workflow on the compose Temporal and PostgreSQL.

Each test reproduces one finding (#9-#13 and the pipeline P2 items) with the controlled
mock steps of :mod:`.support`; everything else is real. They prove the runtime, never a
phase (AGENTS.md).
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from plm_platform.exceptions import ExceptionQueue
from plm_platform.ledger import BudgetLedger
from plm_platform.pipeline.activities import PlatformOperations
from plm_platform.pipeline.client import start_pipeline
from plm_platform.pipeline.mock import MockPhaseSteps, ScriptedStep, default_scripts
from plm_platform.pipeline.states import PipelineState, Step
from plm_platform.pipeline.types import (
    CancelSignal,
    HoldInfo,
    HoldRequest,
    PipelineOutcome,
    ResumeSignal,
    TransitionAck,
    TransitionRecord,
)
from plm_platform.pipeline.workflow import PipelineWorkflow
from plm_platform.temporal import ValidationFailure, idempotency_key
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from tests.integration.pipeline.support import (
    CURRENCY,
    OPTIONS,
    QUEUE,
    new_run,
    running,
    start_run,
    transitions,
    wait_until,
    worker_for,
)
from tests.integration.pipeline.test_gate_r3_workflow import QUARANTINE, _scripts

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

RESULT_TIMEOUT = 120.0


class FlakyPlatform(PlatformOperations):
    """The platform is down for the first ``failures`` attempts at one transition."""

    def __init__(self, *args: Any, failing_sequence: int, failures: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.failing_sequence = failing_sequence
        self.failures = failures

    async def record_transition(self, record: TransitionRecord) -> TransitionAck:
        if record.sequence == self.failing_sequence and self.failures > 0:
            self.failures -= 1
            raise ConnectionError("database restarting")
        return await super().record_transition(record)


async def test_a_platform_outage_longer_than_the_attempt_limit_does_not_end_the_run(
    client: Client, factory: sessionmaker[Session],
) -> None:
    """#9: a platform activity failing more often than ``platform_maximum_attempts``
    (a database restart) is retried until the platform is back; the run completes."""
    platform = FlakyPlatform(
        factory, queue=ExceptionQueue(), ledger=BudgetLedger(factory, currency=CURRENCY),
        failing_sequence=2, failures=OPTIONS.platform_maximum_attempts)
    steps = MockPhaseSteps(_scripts())
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert platform.failures == 0


async def test_a_write_costs_refusal_holds_the_run_and_resumes_after_the_fix(
    client: Client, factory: sessionmaker[Session], platform: PlatformOperations,
) -> None:
    """#9: no ledger currency makes write_costs refuse after the video is public; the
    run holds as a platform fault instead of failing, and completes once fixed."""
    broken = PlatformOperations(factory, queue=platform._queue, ledger=None)
    steps = MockPhaseSteps(_scripts())
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, broken)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the cost-read hold")
        assert held.held_from == "LEARNING"
        assert held.last_error_code == "STEP_PLATFORM_FAULT"
        broken._ledger = BudgetLedger(factory, currency=CURRENCY)  # the operator's fix
        await handle.signal(PipelineWorkflow.resume, ResumeSignal(
            actor="otto", role="platform_owner", evidence={"platform_fix_ref": "ledger-set"}))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed


async def test_slow_processing_is_polled_past_the_step_attempt_limit(
    client: Client, factory: sessionmaker[Session], platform: PlatformOperations,
) -> None:
    """#10: await_processing not ready for more attempts than ``step_maximum_attempts``
    keeps polling (bounded by time) instead of holding as STEP_RETRIES_EXHAUSTED."""
    not_ready = OPTIONS.step_maximum_attempts + 1
    scripts = _scripts()
    scripts[Step.AWAIT_PROCESSING] = dataclasses.replace(
        scripts[Step.AWAIT_PROCESSING], transient_failures=not_ready)
    steps = MockPhaseSteps(scripts)
    directive_id, workflow_id = new_run()
    options = dataclasses.replace(OPTIONS, waiting_step_initial_interval_seconds=0.1,
                                  waiting_step_maximum_interval_seconds=0.5)
    async with running(worker_for(client, steps, platform)):
        handle = await start_pipeline(client, directive_id=directive_id,
                                      brand_id="gate_brand", task_queue=QUEUE,
                                      options=options)
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert len(steps.calls_for(Step.AWAIT_PROCESSING)) == not_ready + 1
    assert not [t for t in transitions(factory, workflow_id)
                if t.to_state is PipelineState.HELD]


async def test_evaluation_waits_for_final_analytics_past_the_step_attempt_limit(
    client: Client, factory: sessionmaker[Session], platform: PlatformOperations,
) -> None:
    """#35: evaluate_outcome not ready (P7's ``analytics_not_final``) for more attempts
    than ``step_maximum_attempts`` keeps polling instead of holding."""
    not_ready = OPTIONS.step_maximum_attempts + 1
    scripts = _scripts()
    scripts[Step.EVALUATE_OUTCOME] = dataclasses.replace(
        scripts[Step.EVALUATE_OUTCOME], transient_failures=not_ready)
    steps = MockPhaseSteps(scripts)
    directive_id, workflow_id = new_run()
    options = dataclasses.replace(OPTIONS, waiting_step_initial_interval_seconds=0.1,
                                  waiting_step_maximum_interval_seconds=0.5,
                                  evaluate_poll_maximum_seconds=0.5)
    async with running(worker_for(client, steps, platform)):
        handle = await start_pipeline(client, directive_id=directive_id,
                                      brand_id="gate_brand", task_queue=QUEUE,
                                      options=options)
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert len(steps.calls_for(Step.EVALUATE_OUTCOME)) == not_ready + 1
    assert not [t for t in transitions(factory, workflow_id)
                if t.to_state is PipelineState.HELD]


async def test_a_cleared_quarantine_hold_resolves_the_p5_case_with_the_evidence(
    client: Client, factory: sessionmaker[Session], platform: PlatformOperations,
) -> None:
    """#11: the reviewer's resume of a QA quarantine hold resolves P5's quarantine case
    (``qa_resolve``) with the reviewer's role and evidence before the run passes QA."""
    quarantine = ScriptedStep(outputs={"disposition": "QUARANTINED", "holds": [
        {"reason_code": "UNKNOWN_COMMERCIAL_LICENSE",
         "details": {"candidate_id": "cand-mock-1", "quarantine_case_id": "qcase-1"}}]},
        provider_ids={"gate_decision_id": "gate-q"})
    steps = MockPhaseSteps(_scripts(qa=quarantine))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the quarantine")
        with factory() as session:
            case = ExceptionQueue().require(session, held.open_case_ids[0])
        evidence = {name: f"ref-{name}" for name in case.required_evidence}
        await handle.signal(PipelineWorkflow.resume, ResumeSignal(
            actor="rita", role="rights_reviewer", evidence=evidence))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert steps.calls_for(Step.QA_RESOLVE) == [idempotency_key(workflow_id, "qa_resolve")]
    resolved = next(t for t in transitions(factory, workflow_id)
                    if t.step == Step.QA_RESOLVE.value)
    assert resolved.to_state is PipelineState.QA
    assert resolved.inputs == {"quarantine_case_ids": ["qcase-1"], "actor": "rita",
                               "role": "rights_reviewer", "evidence": evidence}


async def test_cancel_stops_a_running_step(
    client: Client, factory: sessionmaker[Session], platform: PlatformOperations,
) -> None:
    """#12: a Cancel while the upload step runs cancels the step and ends the run now,
    instead of waiting for the upload to finish (and publish)."""
    scripts = _scripts()
    scripts[Step.UPLOAD] = dataclasses.replace(
        scripts[Step.UPLOAD], block_after_effect_on_first_attempt=True)
    steps = MockPhaseSteps(scripts)
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        await asyncio.wait_for(steps.blocked.wait(), 60)
        await handle.signal(PipelineWorkflow.cancel, CancelSignal(actor="pia",
                                                                   reason="owner Cancel"))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), 30)

    assert outcome.final_state == "CANCELLED"
    assert steps.calls_for(Step.UPLOAD) == [idempotency_key(workflow_id, "upload")]
    assert steps.calls_for(Step.SUBMIT_DISTRIBUTION) == []


class SlowHeldPlatform(PlatformOperations):
    """Recording HELD takes a while after the hold's case is raised (and OPEN)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.raised: asyncio.Queue[HoldInfo] = asyncio.Queue()

    async def raise_hold(self, request: HoldRequest) -> HoldInfo:
        info = await super().raise_hold(request)
        await self.raised.put(info)
        return info

    async def record_transition(self, record: TransitionRecord) -> TransitionAck:
        if record.to_state == PipelineState.HELD.value and record.from_state != "HELD":
            await asyncio.sleep(3)
        return await super().record_transition(record)


async def test_a_resume_sent_while_the_hold_forms_is_not_dropped(
    client: Client, factory: sessionmaker[Session], platform: PlatformOperations,
) -> None:
    """#13: the case is OPEN before the run records HELD; a resume sent in that window
    used to be ignored, leaving the run HELD although the Studio said "sent"."""
    slow = SlowHeldPlatform(factory, queue=platform._queue, ledger=platform._ledger)
    steps = MockPhaseSteps(_scripts(qa=QUARANTINE))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, slow)):
        handle = await start_run(client, directive_id, workflow_id)
        hold = await asyncio.wait_for(slow.raised.get(), 60)
        await handle.signal(PipelineWorkflow.resume, ResumeSignal(
            actor="rita", role="rights_reviewer",
            evidence={name: f"ref-{name}" for name in hold.required_evidence}))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), 60)

    assert outcome.completed


async def test_cancelling_a_held_run_withdraws_its_open_cases(
    client: Client, factory: sessionmaker[Session], platform: PlatformOperations,
) -> None:
    """P2: a cancelled held run closes its cases (WITHDRAWN) instead of leaving them
    OPEN in the queue."""
    steps = MockPhaseSteps(_scripts(qa=QUARANTINE))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the quarantine")
        await handle.signal(PipelineWorkflow.cancel, CancelSignal(actor="pia",
                                                                   reason="not this one"))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), 60)

    assert outcome.final_state == "CANCELLED"
    with factory() as session:
        case = ExceptionQueue().require(session, held.open_case_ids[0])
    assert case.status.value == "WITHDRAWN"


async def test_songs_taken_out_on_a_hold_that_cannot_swap_are_recorded_as_ignored(
    client: Client, factory: sessionmaker[Session], platform: PlatformOperations,
) -> None:
    """P2: exclusions sent with the resume of an upload fault hold (no approval hold,
    so no swap) used to vanish; the transition that leaves the hold names them."""
    upload = default_scripts()[Step.UPLOAD]
    steps = MockPhaseSteps(_scripts(upload=ScriptedStep(
        outputs=upload.outputs, provider_ids=upload.provider_ids, cost=upload.cost,
        raises_first=ValidationFailure("P7 refused the package"), raises_for_first_keys=1)))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the upload hold")
        (case_id,) = held.open_case_ids
        await handle.signal(PipelineWorkflow.resume, ResumeSignal(
            actor="otto", role="platform_owner", evidence={"platform_fix_ref": "abc123"},
            rewind_to="assemble", exclude_candidates=("cand-x",)))
        # The scripted fault repeats under the same key, so the retry holds again.
        await wait_until(handle, lambda s: s.state in ("ANALYZING", "LEARNING") or (
            s.state == "HELD" and bool(s.open_case_ids) and case_id not in s.open_case_ids),
            "the upload retry")
        await handle.terminate(reason="review test: ignored exclusions recorded")

    left = next(t for t in transitions(factory, workflow_id)
                if t.from_state is PipelineState.HELD and t.to_state is PipelineState.UPLOADING)
    assert left.outputs["exclude_candidates_ignored"] == ["cand-x"]
    assert left.outputs["rewind_to"] is None

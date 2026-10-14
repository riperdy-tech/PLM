"""Gate R3: the PART XIV pipeline workflow on the compose Temporal and PostgreSQL.

Controlled mock phase steps (:class:`~plm_platform.pipeline.mock.MockPhaseSteps`) run in
an in-process worker; everything else is real: the Temporal server, the workflow, the
platform activities, the cost ledger, the exception queue and the ``ops`` tables. These
tests prove the runtime (durability, persistence, budget, holds), not the phases: a
mock step is never evidence that a phase works (AGENTS.md).
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from plm_platform.exceptions import ExceptionQueue
from plm_platform.ledger import EntryStatus, budget_entries
from plm_platform.pipeline.activities import PlatformOperations
from plm_platform.pipeline.mock import MockPhaseSteps, ScriptedStep, default_scripts
from plm_platform.pipeline.states import PipelineState, Step
from plm_platform.pipeline.store import PipelineStore
from plm_platform.pipeline.types import (
    ExternalResolutionSignal,
    PipelineOutcome,
    ResumeSignal,
)
from plm_platform.pipeline.workflow import PipelineWorkflow
from plm_platform.temporal import idempotency_key
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError

from tests.integration.pipeline.support import (
    Budget,
    new_run,
    running,
    signal_and_wait,
    start_run,
    transitions,
    wait_until,
    worker_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

RESULT_TIMEOUT = 120.0
HAPPY_PATH = [
    "IDEA", "RESEARCHED", "POLICY_CHECKED", "GENERATING", "GENERATED", "QA", "PASSED",
    "ASSEMBLING", "PACKAGED", "UPLOADING", "UPLOADING", "PROCESSING", "PUBLISHED",
    "ANALYZING", "LEARNING", "LEARNING", "LEARNING",
]
SIDE_EFFECT_STEPS = (Step.RESEARCH, Step.POLICY_CHECK, Step.GENERATE, Step.QA, Step.ASSEMBLE,
                     Step.UPLOAD, Step.SUBMIT_DISTRIBUTION, Step.AWAIT_PROCESSING,
                     Step.EVALUATE_OUTCOME, Step.DISPATCH_REWARD)
MATURES_AFTER = timedelta(seconds=3)


def _scripts(**overrides: ScriptedStep) -> dict[Step, ScriptedStep]:
    """The happy path with a 24h horizon published 24h minus 3s ago, so the durable
    timer has ~3s left to wait when the run reaches ANALYZING."""
    scripts = default_scripts(outcome_horizon="24h",
                              published_offset=-(timedelta(hours=24) - MATURES_AFTER))
    scripts.update({Step(name): script for name, script in overrides.items()})
    return scripts


def _entries(factory: sessionmaker[Session], workflow_id: str) -> list[sa.Row[tuple[object, ...]]]:
    with factory() as session:
        return list(session.execute(
            sa.select(budget_entries).where(
                budget_entries.c.idempotency_key.like(f"{workflow_id}:%"))
            .order_by(budget_entries.c.capability)))


def _assert_contiguous(factory: sessionmaker[Session], workflow_id: str) -> None:
    rows = transitions(factory, workflow_id)
    assert [t.sequence for t in rows] == list(range(1, len(rows) + 1))
    for previous, current in itertools.pairwise(rows):
        assert current.from_state == previous.to_state


async def test_happy_path_reaches_learning_and_persists_every_transition(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: one run walks IDEA -> ... -> LEARNING on Temporal; every transition is in
    ``ops`` in order with its step, attempt, timestamps, provider ids, and the paid steps'
    costs as Money with a basis (provider-reported vs the reserved estimate); the ledger
    holds one committed entry per paid step; LEARNING starts only after the declared
    outcome horizon elapsed after publication (durable timer); a second run for the same
    directive is refused at start."""
    steps = MockPhaseSteps(_scripts())
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)
        # One run per directive: its ledger keys, effects and history belong to that run.
        with pytest.raises(WorkflowAlreadyStartedError):
            await start_run(client, directive_id, workflow_id)

    assert outcome.completed and outcome.final_state == "LEARNING"
    rows = transitions(factory, workflow_id)
    assert [t.to_state.value for t in rows] == HAPPY_PATH
    _assert_contiguous(factory, workflow_id)
    assert rows[-1].final and rows[-1].step == Step.DISPATCH_REWARD.value

    by_step = {t.step: t for t in rows if t.step is not None and t.attempt > 0}
    for step in SIDE_EFFECT_STEPS:
        recorded = by_step[step.value]
        assert recorded.provider_ids, f"{step.value} recorded no provider ids"
        assert recorded.idempotency_key == idempotency_key(workflow_id, step.value)
        assert recorded.attempt == 1 and recorded.retry_count == 0
        assert recorded.started_at is not None and recorded.occurred_at is not None
        assert recorded.started_at <= recorded.occurred_at

    generated = by_step[Step.GENERATE.value]
    assert generated.to_state is PipelineState.GENERATED
    assert generated.inputs == {"policy_check": {"validated": True, "package_ids": ["pkg-mock"]}}
    assert generated.cost is not None
    assert (Decimal(generated.cost.amount), generated.cost.currency) == (Decimal("2.5"), "USD")
    assert generated.cost_basis == "provider_reported"
    uploaded = by_step[Step.UPLOAD.value]
    assert uploaded.cost is not None and Decimal(uploaded.cost.amount) == Decimal("0.5")
    assert uploaded.cost_basis == "reserved_estimate"  # the mock reported no upload cost
    submitted = by_step[Step.SUBMIT_DISTRIBUTION.value]
    assert submitted.cost is not None and submitted.cost_basis == "provider_reported"
    for unpaid in (Step.RESEARCH, Step.QA, Step.EVALUATE_OUTCOME):
        assert by_step[unpaid.value].cost is None  # nothing reported: no invented zero

    entries = _entries(factory, workflow_id)
    assert sorted(e.capability for e in entries) == [
        "distribution.submit", "music.generate", "youtube.upload"]
    assert {e.status for e in entries} == {EntryStatus.COMMITTED.value}

    analyzing = next(t for t in rows if t.to_state is PipelineState.ANALYZING)
    learning = next(t for t in rows if t.to_state is PipelineState.LEARNING)
    matures_at = datetime.fromisoformat(analyzing.outputs["matures_at"])
    published_at = datetime.fromisoformat(analyzing.outputs["published_at"])
    assert matures_at - published_at == timedelta(hours=24)
    assert learning.occurred_at is not None and learning.occurred_at >= matures_at
    assert analyzing.occurred_at is not None and analyzing.occurred_at < matures_at

    with factory() as session:
        run = PipelineStore().run(session, workflow_id)
    assert run is not None and run.state is PipelineState.LEARNING
    assert run.completed_at is not None and run.last_sequence == len(HAPPY_PATH)


async def test_worker_killed_mid_activity_recovers_with_each_effect_exactly_once(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: the worker running a paid activity is shut down after the activity's
    effect but before it returned; the run stays RUNNING on the server; a new worker
    picks it up, the activity is retried with the same idempotency key, and every
    side-effecting step's effect happened exactly once; the ledger holds one entry per
    paid step and no transition is duplicated by the replay on the new worker."""
    steps = MockPhaseSteps(_scripts(
        generate=ScriptedStep(**{**default_scripts()[Step.GENERATE].__dict__,
                                 "block_after_effect_on_first_attempt": True})))
    directive_id, workflow_id = new_run()
    first = worker_for(client, steps, platform, identity=f"gate-a-{uuid.uuid4().hex[:6]}")
    async with running(first):
        handle = await start_run(client, directive_id, workflow_id)
        await asyncio.wait_for(steps.blocked.wait(), 60)
        generate_key = idempotency_key(workflow_id, Step.GENERATE.value)
        assert steps.effect_counts[generate_key] == 1
    # The first worker is gone mid-activity: the run is still open on the server and its
    # durable state says GENERATING (no worker is polling, so the store answers, not a query).
    description = await handle.describe()
    assert description.status == WorkflowExecutionStatus.RUNNING
    assert transitions(factory, workflow_id)[-1].to_state is PipelineState.GENERATING

    second = worker_for(client, steps, platform, identity=f"gate-b-{uuid.uuid4().hex[:6]}")
    async with running(second):
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert len(steps.calls_for(Step.GENERATE)) >= 2, "the killed attempt was never retried"
    for step in SIDE_EFFECT_STEPS:
        key = idempotency_key(workflow_id, step.value)
        assert steps.effect_counts[key] == 1, f"{step.value} effect count"
    rows = transitions(factory, workflow_id)
    assert [t.to_state.value for t in rows] == HAPPY_PATH
    _assert_contiguous(factory, workflow_id)
    generated = next(t for t in rows if t.step == Step.GENERATE.value
                     and t.to_state is PipelineState.GENERATED)
    assert generated.attempt >= 2 and generated.retry_count == generated.attempt - 1
    entries = _entries(factory, workflow_id)
    assert len(entries) == 3 and {e.status for e in entries} == {"committed"}


async def test_exhausted_budget_holds_the_run_and_never_calls_the_paid_activity(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: with the generation ceiling exhausted the reservation is refused, a
    BUDGET_RESERVATION_REFUSED hold (reviewable, budget owner) is raised, the run is
    HELD from POLICY_CHECKED, the paid generation activity is never called and no ledger
    entry exists for it. After the operator raises the ceiling and the budget owner
    resumes with the evidence, the run reserves again and completes."""
    ceiling_id = budget.set_capability_ceiling("music.generate", "0")
    steps = MockPhaseSteps(_scripts())
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the budget hold")
        assert held.held_from == PipelineState.POLICY_CHECKED.value
        assert held.last_error_code == "capability_budget_exhausted"
        await asyncio.sleep(1.0)  # give a wrongly-called paid activity time to show up
        assert steps.calls_for(Step.GENERATE) == []
        assert [e for e in _entries(factory, workflow_id) if e.capability == "music.generate"] == []

        with factory() as session:
            case = ExceptionQueue().require(session, held.open_case_ids[0])
        assert case.reason_code == "BUDGET_RESERVATION_REFUSED"
        assert case.hold_class.value == "REVIEWABLE_QUARANTINE"
        assert case.reviewer_role == "budget_owner" and case.origin_phase == "platform"
        assert case.details["ledger_reason"] == "capability_budget_exhausted"
        rows = transitions(factory, workflow_id)
        assert rows[-1].to_state is PipelineState.HELD and rows[-1].hold_case_ids == (case.id,)

        budget.set_capability_ceiling("music.generate", "10")  # the operator's change
        await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.resume, ResumeSignal(actor="olga", role="budget_owner", evidence={
                "budget_change_ref": ceiling_id, "budget_approval_ref": "approval-1"})))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert len(steps.calls_for(Step.GENERATE)) == 1
    states = [t.to_state.value for t in transitions(factory, workflow_id)]
    assert states[:6] == ["IDEA", "RESEARCHED", "POLICY_CHECKED", "HELD", "POLICY_CHECKED",
                          "GENERATING"]


QUARANTINE = ScriptedStep(outputs={"disposition": "QUARANTINED", "holds": [
    {"reason_code": "UNKNOWN_COMMERCIAL_LICENSE", "details": {"candidate_id": "cand-mock-1"}}]},
    provider_ids={"gate_decision_id": "gate-q"})
HARD_STOP = ScriptedStep(outputs={"disposition": "QUARANTINED", "holds": [
    {"reason_code": "COPYRIGHT_DISPUTE", "details": {"dispute_id": "D-1"}}]},
    provider_ids={"gate_decision_id": "gate-h"})


async def test_reviewable_quarantine_resumes_only_for_an_authorized_reviewer_with_evidence(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: a QA quarantine raises its hold and the run is HELD from QA; a resume by an
    actor without the reviewer role, then by the reviewer without the required evidence,
    is refused, recorded (case events and HELD->HELD transitions with the refusal code)
    and leaves the run HELD; the authorized reviewer with every evidence item resumes it
    and the run completes."""
    steps = MockPhaseSteps(_scripts(qa=QUARANTINE))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the quarantine")
        assert held.held_from == "QA"
        (case_id,) = held.open_case_ids

        refused = await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.resume, ResumeSignal(actor="mallory", role="rights_reviewer")))
        assert refused.state == "HELD" and refused.last_error_code == "reviewer_not_authorized"
        refused = await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.resume, ResumeSignal(actor="rita", role="rights_reviewer")))
        assert refused.state == "HELD" and refused.last_error_code == "evidence_missing"

        with factory() as session:
            case = ExceptionQueue().require(session, case_id)
        evidence = {name: f"ref-{name}" for name in case.required_evidence}
        await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.resume,
            ResumeSignal(actor="rita", role="rights_reviewer", evidence=evidence)))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    with factory() as session:
        queue = ExceptionQueue()
        events = queue.history(session, case_id)
        final_case = queue.require(session, case_id)
    assert [(e.event_type.value, e.decision_code) for e in events] == [
        ("RAISED", None),
        ("RESUME_REFUSED", "reviewer_not_authorized"),
        ("RESUME_REFUSED", "evidence_missing"),
        ("RESUME_AUTHORIZED", "reviewer_authorized"),
        ("RESOLVED", "reviewer_authorized"),
    ]
    assert final_case.status.value == "RESOLVED"
    rows = transitions(factory, workflow_id)
    held_rows = [t for t in rows if t.to_state is PipelineState.HELD]
    assert [t.error_code for t in held_rows] == [
        "qa_quarantined", "reviewer_not_authorized", "evidence_missing"]
    assert [t.to_state.value for t in rows[5:10]] == ["QA", "HELD", "HELD", "HELD", "QA"]
    assert rows[10].to_state is PipelineState.PASSED


async def test_hard_stop_refuses_a_generic_approve_and_proceeds_only_on_external_evidence(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: a COPYRIGHT_DISPUTE hard stop refuses a resume from an admin (generic
    approve) and even from the case's own reviewer role with full evidence
    (hard_stop_not_overridable), and refuses an external resolution from an actor without
    the role; it proceeds only when the rights counsel records the external process's
    CLEARED outcome with its evidence."""
    steps = MockPhaseSteps(_scripts(qa=HARD_STOP))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the hard stop")
        (case_id,) = held.open_case_ids
        with factory() as session:
            case = ExceptionQueue().require(session, case_id)
        assert case.hold_class.value == "HARD_STOP"
        evidence = {name: f"ref-{name}" for name in case.required_evidence}

        for actor, role in (("admin", "admin"), ("counsel", "rights_counsel")):
            refused = await signal_and_wait(handle, lambda a=actor, r=role: handle.signal(
                PipelineWorkflow.resume, ResumeSignal(actor=a, role=r, evidence=evidence)))
            assert refused.state == "HELD"
            assert refused.last_error_code == "hard_stop_not_overridable"
        refused = await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.external_resolution,
            ExternalResolutionSignal(actor="admin", role="admin", evidence=evidence)))
        assert refused.state == "HELD" and refused.last_error_code == "reviewer_role_mismatch"

        await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.external_resolution,
            ExternalResolutionSignal(actor="counsel", role="rights_counsel", evidence=evidence,
                                     outcome="CLEARED", notes="dispute D-1 withdrawn")))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    with factory() as session:
        queue = ExceptionQueue()
        events = [(e.event_type.value, e.decision_code) for e in queue.history(session, case_id)]
        final_case = queue.require(session, case_id)
    assert events[:5] == [
        ("RAISED", None),
        ("RESUME_REFUSED", "hard_stop_not_overridable"),
        ("RESUME_REFUSED", "hard_stop_not_overridable"),
        ("EXTERNAL_RESOLUTION_REFUSED", "reviewer_role_mismatch"),
        ("EXTERNAL_RESOLUTION_RECORDED", "external_resolution_recorded"),
    ]
    assert final_case.resolution is not None
    assert final_case.resolution.value == "CLEARED_BY_EXTERNAL_PROCESS"
    assert all(e[0] != "RESOLVED" for e in events)  # never closed by an approve

"""R5: the playlist top-up loop and the single write-boundary reservation, on compose
Temporal and PostgreSQL.

Controlled mock phase steps (:class:`~plm_platform.pipeline.mock.MockPhaseSteps`) run in
an in-process worker; the Temporal server, the workflow, the platform activities, the
ledger, the exception queue and the ``ops`` tables are real. Where a test funds writes,
the mock's paid steps go through a real ``ExternalWriteGuard`` over the ledger, standing
in for the phase's guard. These prove the workflow's decisions, not the phases.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa
from plm_platform.exceptions import ExceptionQueue
from plm_platform.ledger import EntryStatus, budget_entries
from plm_platform.pipeline.activities import PlatformOperations
from plm_platform.pipeline.http import PlaylistSupplyShort
from plm_platform.pipeline.mock import MockPhaseSteps, ScriptedStep, default_scripts
from plm_platform.pipeline.phase_client import PhaseRequestRefused
from plm_platform.pipeline.states import PipelineState, Step
from plm_platform.pipeline.types import CancelSignal, PipelineOutcome, ResumeSignal
from plm_platform.pipeline.workflow import PipelineWorkflow, topup_count
from plm_platform.temporal import Phase, idempotency_key
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from tests.integration.pipeline.support import (
    Budget,
    guarded_steps,
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
MATURES_AFTER = timedelta(seconds=3)
SHORTAGE = {"p3_package_id": "pkg-mock", "missing_seconds": 301, "shortest_track_seconds": 150,
            "track_spec_ids": ["ts-1"], "cleared_count": 2}


def _scripts(**overrides: ScriptedStep) -> dict[Step, ScriptedStep]:
    scripts = default_scripts(outcome_horizon="24h",
                              published_offset=-(timedelta(hours=24) - MATURES_AFTER))
    scripts.update({Step(name): script for name, script in overrides.items()})
    return scripts


def _short_for(rounds: int) -> ScriptedStep:
    """Assemble refuses ``playlist_supply_short`` for its first ``rounds`` keys."""
    script = default_scripts()[Step.ASSEMBLE]
    return ScriptedStep(outputs=script.outputs, provider_ids=script.provider_ids,
                        raises_first=PlaylistSupplyShort([SHORTAGE], []),
                        raises_for_first_keys=rounds)


def _key(workflow_id: str, name: str) -> str:
    return idempotency_key(workflow_id, name)


def _generate_entries(factory: sessionmaker[Session], workflow_id: str) -> list[sa.Row[tuple[object, ...]]]:
    with factory() as session:
        return list(session.execute(
            sa.select(budget_entries).where(
                budget_entries.c.idempotency_key.like(f"{workflow_id}:%"),
                budget_entries.c.capability == "music.generate")
            .order_by(budget_entries.c.idempotency_key)))


async def test_a_short_playlist_is_topped_up_then_published(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: P6's short supply makes the run extend, generate, QA and assemble again
    inside ASSEMBLING, each round under its own idempotency keys, with the extension sized
    ``ceil(missing / shortest track) + spare``; once the supply suffices the run publishes
    and completes, with no hold raised."""
    steps = MockPhaseSteps(_scripts(assemble=_short_for(2)))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    rows = transitions(factory, workflow_id)
    assert not [t for t in rows if t.to_state is PipelineState.HELD]
    assembling = [(t.step, t.idempotency_key, t.error_code) for t in rows
                  if t.from_state is PipelineState.ASSEMBLING
                  and t.to_state is PipelineState.ASSEMBLING]
    assert assembling == [
        ("assemble", _key(workflow_id, "assemble"), "playlist_supply_short"),
        ("extend", _key(workflow_id, "extend.r1.0"), None),
        ("generate", _key(workflow_id, "generate.r1"), None),
        ("qa", _key(workflow_id, "qa.r1"), None),
        ("assemble", _key(workflow_id, "assemble.r1"), "playlist_supply_short"),
        ("extend", _key(workflow_id, "extend.r2.0"), None),
        ("generate", _key(workflow_id, "generate.r2"), None),
        ("qa", _key(workflow_id, "qa.r2"), None),
    ]
    packaged = next(t for t in rows if t.to_state is PipelineState.PACKAGED)
    assert packaged.idempotency_key == _key(workflow_id, "assemble.r2")
    extend = next(t for t in rows if t.step == "extend")
    assert extend.inputs == {"p3_package_id": "pkg-mock", "count": topup_count(301, 150, 1)}
    assert steps.effects_for(Step.GENERATE) == 3  # the first generation and two top-ups
    assert steps.effects_for(Step.EXTEND) == 2


async def test_the_top_up_is_bounded_then_holds_for_a_production_owner(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: after ``max_topup_rounds`` (2) rounds still short, the run holds with
    PLAYLIST_SUPPLY_SHORT (reviewable, production owner, origin P6) and publishes
    nothing; a resume without the role is refused; the production owner's resume with
    the evidence allows another round, after which the run completes."""
    steps = MockPhaseSteps(_scripts(assemble=_short_for(3)))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the supply hold")
        assert held.held_from == PipelineState.ASSEMBLING.value
        assert held.last_error_code == "playlist_supply_short"
        assert steps.effects_for(Step.GENERATE) == 3
        assert steps.calls_for(Step.UPLOAD) == []  # never published short
        (case_id,) = held.open_case_ids
        with factory() as session:
            case = ExceptionQueue().require(session, case_id)
        assert case.reason_code == "PLAYLIST_SUPPLY_SHORT" and case.policy_mapped
        assert case.hold_class.value == "REVIEWABLE_QUARANTINE"
        assert (case.reviewer_role, case.origin_phase) == ("production_owner", "P6")
        assert case.details["topup_rounds_done"] == 2

        refused = await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.resume, ResumeSignal(actor="olga", role="production_owner")))
        assert refused.state == "HELD" and refused.last_error_code == "reviewer_not_authorized"
        evidence = {name: f"ref-{name}" for name in case.required_evidence}
        await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.resume,
            ResumeSignal(actor="pia", role="production_owner", evidence=evidence)))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert steps.effects_for(Step.GENERATE) == 4
    packaged = next(t for t in transitions(factory, workflow_id)
                    if t.to_state is PipelineState.PACKAGED)
    assert packaged.idempotency_key == _key(workflow_id, "assemble.r3")


async def test_p3s_extension_bound_holds_at_once(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: when P3 refuses to extend past its declared bound, the run holds with
    PLAYLIST_SUPPLY_SHORT immediately, without generating for that round."""
    limit = PhaseRequestRefused(Phase.EXPERIMENTS, "/experiments/v1/packages/pkg-mock/extend",
                                409, "package_extension_limit_exceeded", "bound reached")
    steps = MockPhaseSteps(_scripts(assemble=_short_for(5), extend=ScriptedStep(raises=limit)))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the supply hold")
        with factory() as session:
            case = ExceptionQueue().require(session, held.open_case_ids[0])
        assert case.reason_code == "PLAYLIST_SUPPLY_SHORT"
        assert steps.effects_for(Step.GENERATE) == 1  # the first generation only
        await handle.signal(PipelineWorkflow.cancel, CancelSignal(actor="test", reason="done"))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)
    assert outcome.final_state == "CANCELLED"
    rows = transitions(factory, workflow_id)
    assert any(t.step == "extend" and t.error_code == "package_extension_limit_exceeded"
               for t in rows)


async def test_a_top_up_refused_by_budget_holds_and_reserves_each_write_once(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: the generation guard's capability ceiling funds the first generation but
    not the top-up; the run holds with BUDGET_RESERVATION_REFUSED from ASSEMBLING and the
    top-up's write has no ledger entry; after the operator raises the ceiling and the
    budget owner resumes, the top-up is reserved once and committed. The ledger then holds
    exactly one entry per generation key: the pipeline reserved nothing itself."""
    ceiling_id = budget.set_capability_ceiling("music.generate", "2.50")
    steps = guarded_steps(factory, _scripts(assemble=_short_for(1)))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the budget hold")
        assert held.held_from == PipelineState.ASSEMBLING.value
        assert held.last_error_code == "capability_budget_exhausted"
        entries = _generate_entries(factory, workflow_id)
        assert [e.idempotency_key for e in entries] == [_key(workflow_id, "generate")]
        with factory() as session:
            case = ExceptionQueue().require(session, held.open_case_ids[0])
        assert (case.reason_code, case.origin_phase) == ("BUDGET_RESERVATION_REFUSED", "P4")
        assert case.details["idempotency_key"] == _key(workflow_id, "generate.r1")

        budget.set_capability_ceiling("music.generate", "10")
        await signal_and_wait(handle, lambda: handle.signal(
            PipelineWorkflow.resume, ResumeSignal(actor="olga", role="budget_owner", evidence={
                "budget_change_ref": ceiling_id, "budget_approval_ref": "approval-1"})))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    entries = _generate_entries(factory, workflow_id)
    assert [e.idempotency_key for e in entries] == [_key(workflow_id, "generate"),
                                                    _key(workflow_id, "generate.r1")]
    assert {e.status for e in entries} == {EntryStatus.COMMITTED.value}
    assert steps.effects_for(Step.GENERATE) == 2
    top_up = next(t for t in transitions(factory, workflow_id)
                  if t.step == "generate" and t.idempotency_key == _key(workflow_id,
                                                                        "generate.r1")
                  and t.to_state is PipelineState.ASSEMBLING)
    assert top_up.cost is not None and top_up.cost_basis == "provider_reported"


async def test_a_write_without_an_operator_authorization_holds_for_the_platform_owner(
    client: Client, factory: sessionmaker[Session], budget: Budget, platform: PlatformOperations,
) -> None:
    """Proves: a live upload with no operator authorization is refused by the phase's
    guard before any effect or reservation; the run holds with
    EXTERNAL_WRITE_NOT_AUTHORIZED (reviewable, platform owner, origin P7), not with a
    budget hold."""
    steps = guarded_steps(factory, _scripts(),
                          authorized=("music.generate", "distribution.submit"))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the authorization hold")
        assert held.held_from == PipelineState.UPLOADING.value
        assert held.last_error_code == "no_authorization"
        with factory() as session:
            case = ExceptionQueue().require(session, held.open_case_ids[0])
            uploads = list(session.execute(sa.select(budget_entries).where(
                budget_entries.c.idempotency_key.like(f"{workflow_id}:%"),
                budget_entries.c.capability == "youtube.upload")))
        assert (case.reason_code, case.reviewer_role, case.origin_phase) == (
            "EXTERNAL_WRITE_NOT_AUTHORIZED", "platform_owner", "P7")
        assert uploads == [] and steps.effects_for(Step.UPLOAD) == 0
        await handle.signal(PipelineWorkflow.cancel, CancelSignal(actor="test", reason="done"))
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)
    assert outcome.final_state == "CANCELLED"

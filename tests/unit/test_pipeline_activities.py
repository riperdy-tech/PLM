"""Pipeline activity adapters and platform operations, in-process (SQLite ``ops``).

Proves: the Temporal adapters classify failures (every ``NonRetryableError`` subclass is
non-retryable, ``CapabilityUnavailable`` carries its owning stage and ``no_effect``) and
stamp the attempt; the platform operations fail closed on budget, and resume only
through the exception queue's policy. Uses Temporal's ``ActivityEnvironment``, not a
server; the Gate R3 integration tests prove the same against Temporal and PostgreSQL.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from plm_contracts.money import Money
from plm_platform.exceptions import (
    EXCEPTIONS_METADATA,
    ExceptionQueue,
    StaticReviewerDirectory,
)
from plm_platform.ledger import LEDGER_METADATA, BudgetLedger
from plm_platform.pipeline.activities import (
    BUDGET_LEDGER_NOT_CONFIGURED,
    PlatformOperations,
    build_activities,
)
from plm_platform.pipeline.mock import MockPhaseSteps, ScriptedStep, default_scripts
from plm_platform.pipeline.states import PlatformActivity, Step, step_activity_name
from plm_platform.pipeline.store import PIPELINE_METADATA
from plm_platform.pipeline.types import (
    BudgetRequest,
    ExternalResolutionRequest,
    HoldRequest,
    ResumeRequest,
    StepRequest,
)
from plm_platform.temporal import CapabilityUnavailable, PolicyRejection
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", poolclass=sa.pool.StaticPool,
                              connect_args={"check_same_thread": False})

    @sa.event.listens_for(engine, "connect")
    def _attach(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS ops")

    for metadata in (LEDGER_METADATA, EXCEPTIONS_METADATA, PIPELINE_METADATA):
        metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def _named(activities: list[Any]) -> dict[str, Any]:
    return {fn.__temporal_activity_definition.name: fn for fn in activities}


def _platform(factory: sessionmaker[Session], *, ledger: bool = True) -> PlatformOperations:
    directory = StaticReviewerDirectory({"rita": ["rights_reviewer"],
                                         "counsel": ["rights_counsel"]})
    return PlatformOperations(
        factory, queue=ExceptionQueue(reviewers=directory),
        ledger=BudgetLedger(factory, currency="USD") if ledger else None,
    )


def _step_request(step: Step) -> StepRequest:
    return StepRequest(workflow_id="pipeline.d", directive_id="d", brand_id="b",
                       step=step.value, idempotency_key=f"pipeline.d:{step.value}",
                       requested_at=NOW, inputs={})


def test_capability_unavailable_becomes_non_retryable_with_its_stage(
    factory: sessionmaker[Session],
) -> None:
    scripts = default_scripts()
    scripts[Step.QA] = ScriptedStep(raises=CapabilityUnavailable(
        "pipeline.qa", owning_stage="R4", reason="no audio by URI"))
    activities = _named(build_activities(MockPhaseSteps(scripts), _platform(factory)))
    with pytest.raises(ApplicationError) as raised:
        asyncio.run(ActivityEnvironment().run(activities[step_activity_name(Step.QA)],
                                              _step_request(Step.QA)))
    error = raised.value
    assert error.type == "CapabilityUnavailable" and error.non_retryable
    assert error.details[0]["owning_stage"] == "R4"
    assert error.details[0]["no_effect"] is True


def test_every_non_retryable_subclass_ends_the_step_and_others_retry(
    factory: sessionmaker[Session],
) -> None:
    scripts = default_scripts()
    scripts[Step.RESEARCH] = ScriptedStep(raises=PolicyRejection("directive expired"))
    scripts[Step.ASSEMBLE] = ScriptedStep(transient_failures=1)
    activities = _named(build_activities(MockPhaseSteps(scripts), _platform(factory)))
    with pytest.raises(ApplicationError) as refused:
        asyncio.run(ActivityEnvironment().run(activities[step_activity_name(Step.RESEARCH)],
                                              _step_request(Step.RESEARCH)))
    assert refused.value.non_retryable and refused.value.details[0]["no_effect"] is False
    with pytest.raises(ApplicationError) as transient:
        asyncio.run(ActivityEnvironment().run(activities[step_activity_name(Step.ASSEMBLE)],
                                              _step_request(Step.ASSEMBLE)))
    assert transient.value.type == "TransientStepError" and not transient.value.non_retryable


def test_a_retried_step_applies_its_effect_once_and_stamps_the_attempt(
    factory: sessionmaker[Session],
) -> None:
    steps = MockPhaseSteps()
    activities = _named(build_activities(steps, _platform(factory)))
    env = ActivityEnvironment()
    env.info = replace(env.info, attempt=2)
    run = activities[step_activity_name(Step.GENERATE)]
    first = asyncio.run(env.run(run, _step_request(Step.GENERATE)))
    second = asyncio.run(env.run(run, _step_request(Step.GENERATE)))
    assert first.attempt == 2 and second == first
    assert steps.effect_counts["pipeline.d:generate"] == 1


def test_no_ledger_refuses_every_reservation(factory: sessionmaker[Session]) -> None:
    platform = _platform(factory, ledger=False)
    decision = asyncio.run(platform.reserve_budget(
        BudgetRequest(capability="music.generate", idempotency_key="k", requested_at=NOW)))
    assert not decision.allowed and decision.reason == BUDGET_LEDGER_NOT_CONFIGURED


def test_reservation_follows_the_ledger_and_fails_closed(factory: sessionmaker[Session]) -> None:
    platform = _platform(factory)
    request = BudgetRequest(capability="music.generate", idempotency_key="k",
                            requested_at=NOW)
    # No rate for an unpriced write: the cost is unknown, never zero.
    assert asyncio.run(platform.reserve_budget(request)).reason == "cost_estimate_unknown"
    ledger = BudgetLedger(factory, currency="USD")
    ledger.set_rate(capability="music.generate", cost=Money(amount=Decimal(2), currency="USD"),
                    effective_from=NOW - timedelta(days=1), set_by="ops", now=NOW)
    assert asyncio.run(platform.reserve_budget(request)).reason == "no_budget_ceiling"
    start, end = NOW - timedelta(days=1), NOW + timedelta(days=1)
    ledger.set_ceiling(limit=Money(amount=Decimal(1), currency="USD"), period_start=start,
                       period_end=end, set_by="ops", now=NOW)
    assert asyncio.run(platform.reserve_budget(request)).reason == "budget_exhausted"
    ledger.set_ceiling(limit=Money(amount=Decimal(5), currency="USD"), period_start=start,
                       period_end=end, set_by="ops", now=NOW)
    granted = asyncio.run(platform.reserve_budget(request))
    assert granted.allowed and granted.reserved is not None
    assert (Decimal(granted.reserved.amount), granted.reserved.currency) == (Decimal(2), "USD")


def test_resume_is_decided_by_the_queue_policy_and_recorded(
    factory: sessionmaker[Session],
) -> None:
    platform = _platform(factory)
    hold = asyncio.run(platform.raise_hold(HoldRequest(
        origin_phase="P5", subject_ref="pipeline.d:qa:1",
        reason_code="UNKNOWN_COMMERCIAL_LICENSE", raised_by="pipeline", raised_at=NOW)))
    assert hold.hold_class == "REVIEWABLE_QUARANTINE" and hold.created

    def resume(actor: str, role: str, evidence: dict[str, str]) -> Any:
        return asyncio.run(platform.authorize_resume(ResumeRequest(
            case_ids=(hold.case_id,), actor=actor, role=role, evidence=evidence,
            requested_at=NOW)))[0]

    assert resume("mallory", "rights_reviewer", {}).code == "reviewer_not_authorized"
    assert resume("rita", "rights_reviewer", {}).code == "evidence_missing"
    evidence = {name: "ref" for name in hold.required_evidence}
    allowed = resume("rita", "rights_reviewer", evidence)
    assert allowed.allowed and allowed.case_status == "RESOLVED"
    with factory() as session:
        events = [e.event_type.value for e in ExceptionQueue().history(session, hold.case_id)]
    assert events == ["RAISED", "RESUME_REFUSED", "RESUME_REFUSED", "RESUME_AUTHORIZED",
                      "RESOLVED"]


def test_a_hard_stop_refuses_resume_and_clears_only_on_external_evidence(
    factory: sessionmaker[Session],
) -> None:
    platform = _platform(factory)
    hold = asyncio.run(platform.raise_hold(HoldRequest(
        origin_phase="P5", subject_ref="pipeline.d:qa:1", reason_code="COPYRIGHT_DISPUTE",
        raised_by="pipeline", raised_at=NOW)))
    evidence = {name: "ref" for name in hold.required_evidence}
    refused = asyncio.run(platform.authorize_resume(ResumeRequest(
        case_ids=(hold.case_id,), actor="counsel", role="rights_counsel", evidence=evidence,
        requested_at=NOW)))[0]
    assert not refused.allowed and refused.code == "hard_stop_not_overridable"
    cleared = asyncio.run(platform.record_external_resolution(ExternalResolutionRequest(
        case_ids=(hold.case_id,), actor="counsel", role="rights_counsel", outcome="CLEARED",
        evidence=evidence, notes="dispute closed", requested_at=NOW)))[0]
    assert cleared.allowed and cleared.code == "external_resolution_recorded"


def test_platform_activity_names_are_the_declared_ones(factory: sessionmaker[Session]) -> None:
    names = set(_named(build_activities(MockPhaseSteps(), _platform(factory))))
    assert {a.value for a in PlatformActivity} <= names
    assert {step_activity_name(s) for s in Step} <= names

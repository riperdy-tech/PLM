"""The pipeline's activities: one per phase step, plus the platform operations.

:class:`PipelineActivities` is the protocol the workflow is written against. It has two
halves, implemented separately:

* :class:`PhaseSteps`: one coroutine per :class:`~plm_platform.pipeline.states.Step`.
  :class:`~plm_platform.pipeline.mock.MockPhaseSteps` is the controllable test
  implementation; :class:`~plm_platform.pipeline.http.HttpPhaseSteps` calls the phase
  APIs and raises :class:`~plm_platform.temporal.CapabilityUnavailable` where today's
  APIs cannot carry the step.
* :class:`PlatformActivities`: record a transition, reserve / commit / release budget,
  raise a hold, authorize a resume, record an external resolution.
  :class:`PlatformOperations` implements them over ``ops`` with the platform modules
  (:class:`~plm_platform.ledger.BudgetLedger`,
  :class:`~plm_platform.exceptions.ExceptionQueue`, :class:`.store.PipelineStore`).

:func:`build_activities` turns both into named Temporal activities. The adapters:

* stamp the Temporal attempt on every step result;
* heartbeat while a step runs, so a step whose worker died is retried after the
  heartbeat timeout instead of the full start-to-close timeout;
* turn every :class:`~plm_platform.temporal.NonRetryableError` subclass into a
  non-retryable ``ApplicationError`` (Temporal matches non-retryable types by exact
  name; this way no subclass is ever retried by accident), with the attempt, the owning
  stage for :class:`~plm_platform.temporal.CapabilityUnavailable`, and ``no_effect``
  (true only when the failure proves nothing was written: a ``CapabilityUnavailable``,
  or an exception carrying ``no_effect = True``; only then is a reservation released).

Nothing here reads the clock: every instant arrives on the request (``workflow.now()``).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any, Protocol

from plm_contracts.money import Money
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..exceptions import (
    CaseStatus,
    ExceptionQueue,
    ExternalOutcome,
    ExternalResolutionRefusedError,
    ResumeDecision,
    ReviewDecision,
)
from ..ledger import BudgetLedger, LedgerEntry
from ..temporal import CapabilityUnavailable, NonRetryableError, ValidationFailure
from .states import PlatformActivity, Step, step_activity_name
from .store import PipelineStore
from .types import (
    BudgetDecision,
    BudgetRequest,
    BudgetSettlementRequest,
    ExternalResolutionRequest,
    HoldInfo,
    HoldRequest,
    MoneyAmount,
    ResumeRequest,
    ResumeVerdict,
    SettledCost,
    StepRequest,
    StepResult,
    TransitionAck,
    TransitionRecord,
)

__all__ = [
    "BUDGET_LEDGER_NOT_CONFIGURED",
    "PhaseSteps",
    "PipelineActivities",
    "PlatformActivities",
    "PlatformOperations",
    "build_activities",
    "money_amount",
    "to_money",
]

logger = logging.getLogger(__name__)

BUDGET_LEDGER_NOT_CONFIGURED = "budget_ledger_not_configured"
"""Reservation refusal when the worker has no ledger currency: no ledger is never
read as unlimited budget (mirrors :class:`~plm_platform.execution.NoBudgetLedger`)."""

class PhaseSteps(Protocol):
    """One coroutine per phase step. Each takes the step's idempotency key on the request
    and must make a retried call with the same key produce no second effect."""

    async def research(self, request: StepRequest) -> StepResult: ...
    async def policy_check(self, request: StepRequest) -> StepResult: ...
    async def generate(self, request: StepRequest) -> StepResult: ...
    async def qa(self, request: StepRequest) -> StepResult: ...
    async def assemble(self, request: StepRequest) -> StepResult: ...
    async def upload(self, request: StepRequest) -> StepResult: ...
    async def submit_distribution(self, request: StepRequest) -> StepResult: ...
    async def await_processing(self, request: StepRequest) -> StepResult: ...
    async def evaluate_outcome(self, request: StepRequest) -> StepResult: ...
    async def dispatch_reward(self, request: StepRequest) -> StepResult: ...


class PlatformActivities(Protocol):
    """The platform operations, each idempotent under a retried call."""

    async def record_transition(self, record: TransitionRecord) -> TransitionAck: ...
    async def reserve_budget(self, request: BudgetRequest) -> BudgetDecision: ...
    async def commit_budget(self, request: BudgetSettlementRequest) -> SettledCost: ...
    async def release_budget(self, request: BudgetSettlementRequest) -> SettledCost: ...
    async def raise_hold(self, request: HoldRequest) -> HoldInfo: ...
    async def authorize_resume(self, request: ResumeRequest) -> list[ResumeVerdict]: ...
    async def record_external_resolution(
        self, request: ExternalResolutionRequest
    ) -> list[ResumeVerdict]: ...


class PipelineActivities(PhaseSteps, PlatformActivities, Protocol):
    """Everything :class:`~plm_platform.pipeline.workflow.PipelineWorkflow` calls."""


# --- money -------------------------------------------------------------------------------


def to_money(amount: MoneyAmount | None) -> Money | None:
    if amount is None:
        return None
    return Money(amount=Decimal(amount.amount), currency=amount.currency)


def money_amount(money: Money | None) -> MoneyAmount | None:
    if money is None:
        return None
    return MoneyAmount(amount=str(money.amount), currency=money.currency)


# --- the platform operations over ops ----------------------------------------------------


def _verdict(decision: ResumeDecision, status: CaseStatus) -> ResumeVerdict:
    return ResumeVerdict(
        case_id=decision.case_id, allowed=decision.allowed, code=decision.code.value,
        message=decision.message, case_status=status.value,
        missing_evidence=tuple(decision.missing_evidence),
    )


def _settled(entry: LedgerEntry) -> SettledCost:
    return SettledCost(status=entry.status.value, actual=money_amount(entry.actual),
                       basis=entry.actual_basis)


class PlatformOperations:
    """:class:`PlatformActivities` over the ``ops`` schema (one transaction per call).

    ``ledger=None`` (no ``PLM_BUDGET_LEDGER_CURRENCY``) refuses every reservation with
    :data:`BUDGET_LEDGER_NOT_CONFIGURED`. The exception queue's reviewer directory is
    injected on ``queue``; its default grants nobody a role.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        queue: ExceptionQueue,
        ledger: BudgetLedger | None,
        store: PipelineStore | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._ledger = ledger
        self._store = store if store is not None else PipelineStore()

    async def record_transition(self, record: TransitionRecord) -> TransitionAck:
        return await asyncio.to_thread(self._record_transition, record)

    def _record_transition(self, record: TransitionRecord) -> TransitionAck:
        with self._session_factory() as session:
            ack = self._store.record(session, record)
            session.commit()
            return ack

    async def reserve_budget(self, request: BudgetRequest) -> BudgetDecision:
        return await asyncio.to_thread(self._reserve, request)

    def _reserve(self, request: BudgetRequest) -> BudgetDecision:
        if self._ledger is None:
            return BudgetDecision(allowed=False, reason=BUDGET_LEDGER_NOT_CONFIGURED)
        result = self._ledger.reserve(
            request.capability, request.idempotency_key,
            cost=to_money(request.estimate), now=request.requested_at,
        )
        return BudgetDecision(
            allowed=result.allowed, reason=result.reason.value,
            reserved=None if result.entry is None else money_amount(result.entry.reserved),
            remaining=money_amount(result.remaining), replayed=result.replayed,
        )

    async def commit_budget(self, request: BudgetSettlementRequest) -> SettledCost:
        return await asyncio.to_thread(self._commit, request)

    def _commit(self, request: BudgetSettlementRequest) -> SettledCost:
        if self._ledger is None:
            raise ValidationFailure("cannot commit a write without a budget ledger")
        entry = self._ledger.commit(request.capability, request.idempotency_key,
                                    actual=to_money(request.actual), now=request.settled_at)
        return _settled(entry)

    async def release_budget(self, request: BudgetSettlementRequest) -> SettledCost:
        return await asyncio.to_thread(self._release, request)

    def _release(self, request: BudgetSettlementRequest) -> SettledCost:
        if self._ledger is None:
            raise ValidationFailure("cannot release a write without a budget ledger")
        entry = self._ledger.release(request.capability, request.idempotency_key,
                                     reason=request.reason or "write_not_performed",
                                     now=request.settled_at)
        return _settled(entry)

    async def raise_hold(self, request: HoldRequest) -> HoldInfo:
        return await asyncio.to_thread(self._raise_hold, request)

    def _raise_hold(self, request: HoldRequest) -> HoldInfo:
        with self._session_factory() as session:
            result = self._queue.raise_exception(
                session, origin_phase=request.origin_phase, subject_ref=request.subject_ref,
                reason_code=request.reason_code, raised_by=request.raised_by,
                now=request.raised_at, details=dict(request.details),
            )
            session.commit()
        case = result.case
        return HoldInfo(
            case_id=case.id, reason_code=case.reason_code, hold_class=case.hold_class.value,
            reviewer_role=case.reviewer_role, required_evidence=tuple(case.required_evidence),
            status=case.status.value, policy_mapped=case.policy_mapped, created=result.created,
        )

    async def authorize_resume(self, request: ResumeRequest) -> list[ResumeVerdict]:
        return await asyncio.to_thread(self._authorize_resume, request)

    def _authorize_resume(self, request: ResumeRequest) -> list[ResumeVerdict]:
        """The single resume check per case (recorded, allowed or refused); an allowed
        resume of a still-open reviewable case also closes it as resumed."""
        verdicts: list[ResumeVerdict] = []
        with self._session_factory() as session:
            for case_id in request.case_ids:
                decision = self._queue.authorize_resume(
                    session, case_id, actor=request.actor, role=request.role,
                    evidence=request.evidence, now=request.requested_at,
                )
                case = self._queue.require(session, case_id)
                if decision.allowed and case.is_open:
                    case = self._queue.resolve(
                        session, case_id, decision=ReviewDecision.RESUME, actor=request.actor,
                        role=request.role, evidence=request.evidence,
                        notes="resumed through the pipeline workflow's resume signal",
                        now=request.requested_at,
                    )
                verdicts.append(_verdict(decision, case.status))
            session.commit()
        return verdicts

    async def record_external_resolution(
        self, request: ExternalResolutionRequest
    ) -> list[ResumeVerdict]:
        return await asyncio.to_thread(self._record_external_resolution, request)

    def _record_external_resolution(
        self, request: ExternalResolutionRequest
    ) -> list[ResumeVerdict]:
        try:
            outcome = ExternalOutcome(request.outcome)
        except ValueError:
            raise ValidationFailure(
                f"external outcome {request.outcome!r} is not one of "
                f"{[o.value for o in ExternalOutcome]}"
            ) from None
        verdicts: list[ResumeVerdict] = []
        with self._session_factory() as session:
            for case_id in request.case_ids:
                case = self._queue.require(session, case_id)
                if case.is_open:
                    try:
                        self._queue.record_external_resolution(
                            session, case_id, outcome=outcome, evidence=request.evidence,
                            actor=request.actor, role=request.role, notes=request.notes,
                            now=request.requested_at,
                        )
                    except ExternalResolutionRefusedError as refused:
                        # The refusal is already an event on the case; keep it.
                        case = self._queue.require(session, case_id)
                        verdicts.append(_verdict(refused.decision, case.status))
                        continue
                # The same single check every resume passes, now against the closed case.
                decision = self._queue.authorize_resume(
                    session, case_id, actor=request.actor, role=request.role,
                    evidence=request.evidence, now=request.requested_at,
                )
                case = self._queue.require(session, case_id)
                verdicts.append(_verdict(decision, case.status))
            session.commit()
        return verdicts


# --- Temporal adapters --------------------------------------------------------------------


def _failure_details(exc: BaseException, attempt: int) -> dict[str, Any]:
    details: dict[str, Any] = {
        "attempt": attempt,
        # Only a failure that proves nothing was written frees a budget reservation.
        "no_effect": isinstance(exc, CapabilityUnavailable) or getattr(exc, "no_effect", False)
        is True,
    }
    if isinstance(exc, CapabilityUnavailable):
        details.update(capability=exc.capability, owning_stage=exc.owning_stage,
                       reason=exc.reason)
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        details["code"] = code
    return details


def _application_error(exc: Exception, attempt: int) -> ApplicationError:
    return ApplicationError(
        str(exc) or type(exc).__name__,
        _failure_details(exc, attempt),
        type=type(exc).__name__,
        non_retryable=isinstance(exc, NonRetryableError),
    )


async def _call[T](operation: Awaitable[T], attempt: int) -> T:
    try:
        return await operation
    except (ApplicationError, asyncio.CancelledError):
        raise
    except Exception as exc:  # every failure is classified for Temporal
        raise _application_error(exc, attempt) from exc


@contextlib.asynccontextmanager
async def _heartbeating() -> AsyncIterator[None]:
    """Heartbeat at a third of the heartbeat timeout while the step runs."""
    timeout = activity.info().heartbeat_timeout
    interval = 1.0 if timeout is None else max(timeout.total_seconds() / 3.0, 0.2)

    async def beat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(interval)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _step_activity(
    step: Step, method: Callable[[StepRequest], Awaitable[StepResult]]
) -> Callable[[StepRequest], Awaitable[StepResult]]:
    @activity.defn(name=step_activity_name(step))
    async def run_step(request: StepRequest) -> StepResult:
        attempt = activity.info().attempt
        if request.step != step.value:
            raise ApplicationError(
                f"request for {request.step!r} sent to the {step.value!r} activity",
                {"attempt": attempt, "no_effect": True}, type="ValidationFailure",
                non_retryable=True,
            )
        async with _heartbeating():
            result = await _call(method(request), attempt)
        return dataclasses.replace(result, attempt=attempt)

    return run_step


def _platform_activity(
    name: PlatformActivity, method: Callable[[Any], Awaitable[Any]],
    request_type: Any, result_type: Any,
) -> Callable[..., Awaitable[Any]]:
    async def run_operation(request: Any) -> Any:
        return await _call(method(request), activity.info().attempt)

    # Temporal converts the argument and the result by these annotations.
    run_operation.__annotations__ = {"request": request_type, "return": result_type}
    return activity.defn(name=name.value)(run_operation)


_PLATFORM_METHODS: Mapping[PlatformActivity, tuple[str, Any, Any]] = {
    PlatformActivity.RECORD_TRANSITION: ("record_transition", TransitionRecord, TransitionAck),
    PlatformActivity.RESERVE_BUDGET: ("reserve_budget", BudgetRequest, BudgetDecision),
    PlatformActivity.COMMIT_BUDGET: ("commit_budget", BudgetSettlementRequest, SettledCost),
    PlatformActivity.RELEASE_BUDGET: ("release_budget", BudgetSettlementRequest, SettledCost),
    PlatformActivity.RAISE_HOLD: ("raise_hold", HoldRequest, HoldInfo),
    PlatformActivity.AUTHORIZE_RESUME: ("authorize_resume", ResumeRequest, list[ResumeVerdict]),
    PlatformActivity.RECORD_EXTERNAL_RESOLUTION: (
        "record_external_resolution", ExternalResolutionRequest, list[ResumeVerdict]),
}


def build_activities(
    steps: PhaseSteps, platform: PlatformActivities
) -> list[Callable[..., Awaitable[Any]]]:
    """Every activity :class:`~plm_platform.pipeline.workflow.PipelineWorkflow` calls,
    named and wrapped for Temporal."""
    activities: list[Callable[..., Awaitable[Any]]] = [
        _step_activity(step, getattr(steps, step.value)) for step in Step
    ]
    activities.extend(
        _platform_activity(name, getattr(platform, method), request_type, result_type)
        for name, (method, request_type, result_type) in _PLATFORM_METHODS.items()
    )
    return activities

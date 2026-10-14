"""The PART XIV pipeline as one durable Temporal workflow per P2 directive (R3).

Workflow id: ``business_workflow_id("pipeline", directive_id)``. The workflow walks the
P27 lifecycle through the activities in :mod:`.activities` and records every transition
in ``ops`` through the ``record_transition`` activity (sequence numbers are assigned
here, deterministically; the store makes each write idempotent).

* **Steps.** One activity per phase step, each called with
  ``idempotency_key(workflow_id, step)``. Retries come from the Temporal retry policy;
  a non-retryable failure ends the step. ``CapabilityUnavailable`` (today's runtime
  cannot do the step) becomes a ``CAPABILITY_UNAVAILABLE`` hold; any other permanent
  failure, or retries exhausted, moves the run to ``FAILED`` with the error recorded.
* **Budget.** A paid step (generation, upload, distribution submission) first reserves
  its cost in the ledger. A refusal raises a ``BUDGET_RESERVATION_REFUSED`` hold and the
  run enters ``HELD`` without calling the paid activity. After the step the reservation
  is committed with the provider-reported cost (or the reserved estimate, labelled as
  such); it is released only when the failure proves nothing was written.
* **Holds.** A QA quarantine raises the case's holds and enters ``HELD``. The run leaves
  ``HELD`` only when every hold of the episode is cleared: ``resume(actor, role,
  evidence)`` runs ``authorize_resume`` in an activity (a refusal is recorded, the run
  stays ``HELD``); ``external_resolution(...)`` records a hard stop's external evidence.
  A case closed without clearance ends the run (``REJECTED`` from QA, else
  ``CANCELLED``).
* **Outcome.** ``ANALYZING`` waits on a durable timer until the directive's outcome
  horizon has elapsed after publication (``published_at + outcome_horizon``); a
  ``lifetime`` horizon waits for ``outcome_window_closed``. ``LEARNING`` then evaluates
  the outcome and dispatches the reward through the outbox.

Deterministic: time is ``workflow.now()``, there is no randomness, and only the
sandbox-safe vocabulary modules are imported (passed through).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from plm_contracts.enums import ObservationWindow
    from plm_contracts.measurement import window_end

    from ..temporal import NON_RETRYABLE_ERROR_TYPES, idempotency_key
    from .states import (
        PAID_STEP_CAPABILITIES,
        STEP_ORIGIN_PHASE,
        PipelineState,
        PlatformActivity,
        Step,
        step_activity_name,
    )
    from .types import (
        BudgetDecision,
        BudgetRequest,
        BudgetSettlementRequest,
        CancelSignal,
        ExternalResolutionRequest,
        ExternalResolutionSignal,
        HoldInfo,
        HoldRequest,
        OutcomeWindowClosedSignal,
        PipelineInput,
        PipelineOutcome,
        PipelineStatus,
        ResumeRequest,
        ResumeSignal,
        ResumeVerdict,
        SettledCost,
        StepRequest,
        StepResult,
        TransitionAck,
        TransitionRecord,
    )

__all__ = [
    "BUDGET_HOLD_REASON",
    "CAPABILITY_HOLD_REASON",
    "WORKFLOW_NAME",
    "PipelineWorkflow",
]

WORKFLOW_NAME: Final[str] = "PipelineWorkflow"
BUDGET_HOLD_REASON: Final[str] = "BUDGET_RESERVATION_REFUSED"
CAPABILITY_HOLD_REASON: Final[str] = "CAPABILITY_UNAVAILABLE"
"""Not classified in the hold policy today, so it holds as an unmapped HARD_STOP (fail
closed, flagged ``policy_mapped = False``) until the policy owner classifies it."""

@dataclass(frozen=True, slots=True)
class _Failure:
    type: str
    message: str
    attempt: int
    non_retryable: bool
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def no_effect(self) -> bool:
        return self.details.get("no_effect") is True


@dataclass(frozen=True, slots=True)
class _Done:
    result: StepResult
    started_at: datetime
    settled: SettledCost | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    """What the step was given (references to earlier steps' outputs)."""


def _failure(error: ActivityError) -> _Failure:
    cause = error.cause
    if isinstance(cause, ApplicationError):
        details = cause.details[0] if cause.details and isinstance(cause.details[0], dict) else {}
        attempt = details.get("attempt")
        return _Failure(
            type=cause.type or "ApplicationError", message=cause.message,
            attempt=attempt if isinstance(attempt, int) else 0,
            non_retryable=cause.non_retryable, details=dict(details),
        )
    name = type(cause).__name__ if cause is not None else "ActivityError"
    message = str(cause) if cause is not None else str(error)
    return _Failure(type=name, message=message, attempt=0, non_retryable=False,
                    details={"retry_state": str(error.retry_state)})


def _amount_dict(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    return {"amount": value.amount, "currency": value.currency}


@workflow.defn(name=WORKFLOW_NAME)
class PipelineWorkflow:
    def __init__(self) -> None:
        self._input: PipelineInput | None = None
        self._state: PipelineState | None = None
        self._sequence = 0
        self._held_from: PipelineState | None = None
        self._open_cases: tuple[str, ...] = ()
        self._signals: list[ResumeSignal | ExternalResolutionSignal] = []
        self._cancel: CancelSignal | None = None
        self._window_closed: OutcomeWindowClosedSignal | None = None
        self._last_error: str | None = None
        self._hold_episodes: dict[str, int] = {}

    # -- signals and query -----------------------------------------------------------------

    @workflow.signal
    def resume(self, signal: ResumeSignal) -> None:
        """Ask to continue past the open holds. Ignored unless the run is HELD."""
        if self._state is PipelineState.HELD:
            self._signals.append(signal)
        else:
            workflow.logger.warning("resume ignored: run is %s, not HELD", self._state)

    @workflow.signal
    def external_resolution(self, signal: ExternalResolutionSignal) -> None:
        """Record a hard stop's external evidence. Ignored unless the run is HELD."""
        if self._state is PipelineState.HELD:
            self._signals.append(signal)
        else:
            workflow.logger.warning("external_resolution ignored: run is %s", self._state)

    @workflow.signal
    def cancel(self, signal: CancelSignal) -> None:
        self._cancel = signal

    @workflow.signal
    def outcome_window_closed(self, signal: OutcomeWindowClosedSignal) -> None:
        self._window_closed = signal

    @workflow.query
    def status(self) -> PipelineStatus:
        return PipelineStatus(
            state=None if self._state is None else self._state.value,
            sequence=self._sequence,
            held_from=None if self._held_from is None else self._held_from.value,
            open_case_ids=self._open_cases,
            last_error_code=self._last_error,
        )

    # -- the run ---------------------------------------------------------------------------

    @workflow.run
    async def run(self, pipeline_input: PipelineInput) -> PipelineOutcome:
        self._input = pipeline_input
        await self._move(PipelineState.IDEA, inputs={
            "directive_id": pipeline_input.directive_id, "brand_id": pipeline_input.brand_id})

        research = await self._step(Step.RESEARCH, {
            "directive_id": pipeline_input.directive_id, "brand_id": pipeline_input.brand_id})
        if research is None:
            return self._outcome()
        horizon = self._horizon(research.result.outputs.get("outcome_horizon"))
        if horizon is None:
            return await self._fail_fact(Step.RESEARCH, research, "outcome_horizon_missing",
                                         "the directive's outcome_horizon is missing or unknown")
        await self._move(PipelineState.RESEARCHED, step=Step.RESEARCH, done=research)

        policy = await self._step(Step.POLICY_CHECK, {"research": research.result.outputs})
        if policy is None:
            return self._outcome()
        await self._move(PipelineState.POLICY_CHECKED, step=Step.POLICY_CHECK, done=policy)

        generated = await self._paid_step(
            Step.GENERATE, {"policy_check": policy.result.outputs},
            enter=PipelineState.GENERATING)
        if generated is None:
            return self._outcome()
        await self._move(PipelineState.GENERATED, step=Step.GENERATE, done=generated)

        if not await self._qa(generated):
            return self._outcome()

        assembled = await self._step(Step.ASSEMBLE, {"generate": generated.result.outputs},
                                     enter=PipelineState.ASSEMBLING)
        if assembled is None:
            return self._outcome()
        await self._move(PipelineState.PACKAGED, step=Step.ASSEMBLE, done=assembled)

        uploaded = await self._paid_step(Step.UPLOAD, {"assemble": assembled.result.outputs},
                                         enter=PipelineState.UPLOADING)
        if uploaded is None:
            return self._outcome()
        await self._move(PipelineState.UPLOADING, step=Step.UPLOAD, done=uploaded)

        submitted = await self._paid_step(
            Step.SUBMIT_DISTRIBUTION,
            {"assemble": assembled.result.outputs, "upload": uploaded.result.outputs},
            enter=PipelineState.UPLOADING)
        if submitted is None:
            return self._outcome()
        await self._move(PipelineState.PROCESSING, step=Step.SUBMIT_DISTRIBUTION, done=submitted)

        processed = await self._step(
            Step.AWAIT_PROCESSING,
            {"upload": uploaded.result.outputs,
             "submit_distribution": submitted.result.outputs})
        if processed is None:
            return self._outcome()
        published_at = self._instant(processed.result.outputs.get("published_at"))
        if published_at is None:
            return await self._fail_fact(Step.AWAIT_PROCESSING, processed, "published_at_missing",
                                         "the platform reported no timezone-aware published_at")
        await self._move(PipelineState.PUBLISHED, step=Step.AWAIT_PROCESSING, done=processed)

        if not await self._analyze(published_at, horizon):
            return self._outcome()

        outcome_inputs = {
            "research": research.result.outputs, "upload": uploaded.result.outputs,
            "published_at": published_at.isoformat(), "outcome_horizon": horizon.value,
        }
        evaluated = await self._step(Step.EVALUATE_OUTCOME, outcome_inputs)
        if evaluated is None:
            return self._outcome()
        await self._move(PipelineState.LEARNING, step=Step.EVALUATE_OUTCOME, done=evaluated)

        dispatched = await self._step(Step.DISPATCH_REWARD,
                                      {"evaluate_outcome": evaluated.result.outputs})
        if dispatched is None:
            return self._outcome()
        if dispatched.result.outputs.get("delivery_status") != "DELIVERED":
            return await self._fail_fact(
                Step.DISPATCH_REWARD, dispatched, "reward_not_delivered",
                "the reward counts as posted only once its outbox event is DELIVERED")
        await self._move(PipelineState.LEARNING, step=Step.DISPATCH_REWARD, done=dispatched,
                         final=True)
        return self._outcome(completed=True)

    # -- QA and the outcome horizon --------------------------------------------------------

    async def _qa(self, generated: _Done) -> bool:
        await self._move(PipelineState.QA)
        checked = await self._step(Step.QA, {"generate": generated.result.outputs})
        if checked is None:
            return False
        disposition = checked.result.outputs.get("disposition")
        if disposition == "PASSED":
            await self._move(PipelineState.PASSED, step=Step.QA, done=checked)
            return True
        if disposition == "REJECTED":
            await self._move(PipelineState.REJECTED, step=Step.QA, done=checked, final=True,
                             error_code="qa_rejected", error_detail="P5 rejected the candidate")
            return False
        holds = checked.result.outputs.get("holds")
        if disposition != "QUARANTINED" or not isinstance(holds, list) or not holds or not all(
            isinstance(h, dict) and isinstance(h.get("reason_code"), str) and h["reason_code"]
            for h in holds
        ):
            # A missing or unknown disposition is never a pass (AGENTS.md).
            await self._fail_fact(Step.QA, checked, "qa_disposition_invalid",
                                  f"disposition {disposition!r} with holds {holds!r}")
            return False
        specs = [(STEP_ORIGIN_PHASE[Step.QA], str(h["reason_code"]), dict(h.get("details") or {}))
                 for h in holds]
        if not await self._hold(Step.QA, specs, error_code="qa_quarantined", done=checked):
            return False
        await self._move(PipelineState.PASSED, step=Step.QA,
                         outputs={"cleared_by_hold_resolution": True,
                                  "disposition": "QUARANTINED"})
        return True

    async def _analyze(self, published_at: datetime, horizon: ObservationWindow) -> bool:
        matures_at = window_end(published_at, horizon)
        await self._move(PipelineState.ANALYZING, outputs={
            "published_at": published_at.isoformat(), "outcome_horizon": horizon.value,
            "matures_at": None if matures_at is None else matures_at.isoformat()})
        if matures_at is None:
            await workflow.wait_condition(
                lambda: self._window_closed is not None or self._cancel is not None)
        else:
            delay = matures_at - workflow.now()
            if delay > timedelta(0):
                try:
                    # A durable timer: survives worker restarts; cancel wakes it early.
                    await workflow.wait_condition(lambda: self._cancel is not None,
                                                  timeout=delay)
                except TimeoutError:
                    pass
        if self._cancel is not None:
            await self._cancelled()
            return False
        closed = self._window_closed
        await self._move(PipelineState.LEARNING, outputs={
            "matured_at": workflow.now().isoformat(),
            "matures_at": None if matures_at is None else matures_at.isoformat(),
            "window_closed_by": None if closed is None else closed.actor})
        return True

    # -- steps -----------------------------------------------------------------------------

    def _request(self, step: Step, inputs: dict[str, Any]) -> StepRequest:
        assert self._input is not None
        workflow_id = workflow.info().workflow_id
        return StepRequest(
            workflow_id=workflow_id, directive_id=self._input.directive_id,
            brand_id=self._input.brand_id, step=step.value,
            idempotency_key=idempotency_key(workflow_id, step.value),
            requested_at=workflow.now(), inputs=inputs,
        )

    async def _call(self, step: Step, inputs: dict[str, Any]) -> StepResult | _Failure:
        assert self._input is not None
        options = self._input.options
        try:
            result: StepResult = await workflow.execute_activity(
                step_activity_name(step), self._request(step, inputs), result_type=StepResult,
                start_to_close_timeout=timedelta(seconds=options.step_start_to_close_seconds),
                heartbeat_timeout=timedelta(seconds=options.step_heartbeat_seconds),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1), backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=60),
                    maximum_attempts=options.step_maximum_attempts,
                    non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
                ),
            )
        except ActivityError as error:
            return _failure(error)
        return result

    async def _step(self, step: Step, inputs: dict[str, Any], *,
                    enter: PipelineState | None = None) -> _Done | None:
        """Run an unpaid step; ``None`` means the run ended (failed, cancelled, closed)."""
        if await self._cancelled():
            return None
        if enter is not None and self._state is not enter:
            await self._move(enter)
        while True:
            started = workflow.now()
            outcome = await self._call(step, inputs)
            if isinstance(outcome, StepResult):
                return _Done(result=outcome, started_at=started, inputs=inputs)
            if outcome.type == "CapabilityUnavailable" and await self._capability_hold(
                    step, outcome):
                continue
            if self._state not in (PipelineState.FAILED, PipelineState.CANCELLED,
                                   PipelineState.REJECTED):
                await self._fail(step, outcome, started)
            return None

    async def _paid_step(self, step: Step, inputs: dict[str, Any], *,
                         enter: PipelineState) -> _Done | None:
        """Reserve, then run, then settle a paid step. A refused reservation holds the run
        and the paid activity is not called."""
        capability = PAID_STEP_CAPABILITIES[step]
        key = idempotency_key(workflow.info().workflow_id, step.value)
        while True:
            if await self._cancelled():
                return None
            decision = await self._reserve(step, capability, key)
            if decision is None:
                return None
            if self._state is not enter:
                await self._move(enter, step=step, outputs={
                    "budget": {"capability": capability, "reason": decision.reason,
                               "reserved": _amount_dict(decision.reserved),
                               "remaining": _amount_dict(decision.remaining),
                               "replayed": decision.replayed}})
            started = workflow.now()
            outcome = await self._call(step, inputs)
            if isinstance(outcome, StepResult):
                settled = await self._platform(
                    PlatformActivity.COMMIT_BUDGET,
                    BudgetSettlementRequest(capability=capability, idempotency_key=key,
                                            settled_at=workflow.now(), actual=outcome.cost),
                    SettledCost)
                return _Done(result=outcome, started_at=started, settled=settled,
                             inputs=inputs)
            if outcome.no_effect:
                await self._platform(
                    PlatformActivity.RELEASE_BUDGET,
                    BudgetSettlementRequest(capability=capability, idempotency_key=key,
                                            settled_at=workflow.now(),
                                            reason=f"{step.value}: {outcome.type}"),
                    SettledCost)
            # Otherwise the write may have happened: the reservation stays held.
            if outcome.type == "CapabilityUnavailable" and await self._capability_hold(
                    step, outcome):
                continue
            if self._state not in (PipelineState.FAILED, PipelineState.CANCELLED,
                                   PipelineState.REJECTED):
                await self._fail(step, outcome, started)
            return None

    async def _reserve(self, step: Step, capability: str, key: str) -> BudgetDecision | None:
        while True:
            decision = await self._platform(
                PlatformActivity.RESERVE_BUDGET,
                BudgetRequest(capability=capability, idempotency_key=key,
                              requested_at=workflow.now()),
                BudgetDecision)
            if decision.allowed:
                return decision
            details = {"capability": capability, "ledger_reason": decision.reason,
                       "remaining": _amount_dict(decision.remaining), "step": step.value}
            if not await self._hold(step, [("platform", BUDGET_HOLD_REASON, details)],
                                    error_code=decision.reason):
                return None

    async def _capability_hold(self, step: Step, failure: _Failure) -> bool:
        details = {"step": step.value, "error": failure.message,
                   "owning_stage": failure.details.get("owning_stage"),
                   "capability": failure.details.get("capability")}
        return await self._hold(step, [(STEP_ORIGIN_PHASE[step], CAPABILITY_HOLD_REASON, details)],
                                error_code=CAPABILITY_HOLD_REASON, error_detail=failure.message,
                                attempt=failure.attempt)

    # -- holds -----------------------------------------------------------------------------

    async def _hold(self, step: Step, specs: list[tuple[str, str, dict[str, Any]]], *,
                    error_code: str, error_detail: str | None = None,
                    done: _Done | None = None, attempt: int = 0) -> bool:
        """Raise the holds, enter HELD, and wait until all are cleared (``True``) or the
        run ends (``False``)."""
        workflow_id = workflow.info().workflow_id
        episode = self._hold_episodes.get(step.value, 0) + 1
        self._hold_episodes[step.value] = episode
        # The run id keeps a hold from ever matching a case raised by an earlier run of the
        # same directive (a closed case must never answer a new hold's resume).
        subject = f"{workflow_id}:{workflow.info().run_id}:{step.value}:{episode}"
        self._signals.clear()  # nothing sent before this hold existed may decide it
        cases: list[HoldInfo] = []
        for origin, reason_code, details in specs:
            cases.append(await self._platform(
                PlatformActivity.RAISE_HOLD,
                HoldRequest(origin_phase=origin, subject_ref=subject, reason_code=reason_code,
                            raised_by=f"pipeline:{workflow_id}", raised_at=workflow.now(),
                            details=details),
                HoldInfo))
        held_from = self._state
        assert held_from is not None
        hold_outputs: dict[str, Any] = {"holds": [
            {"case_id": c.case_id, "reason_code": c.reason_code, "hold_class": c.hold_class,
             "reviewer_role": c.reviewer_role, "required_evidence": list(c.required_evidence),
             "policy_mapped": c.policy_mapped} for c in cases]}
        if done is not None:
            hold_outputs["step_outputs"] = done.result.outputs
        await self._move(PipelineState.HELD, step=step, done=done, outputs=hold_outputs,
                         error_code=error_code, error_detail=error_detail,
                         hold_case_ids=tuple(c.case_id for c in cases), held_from=held_from,
                         attempt=attempt)
        self._open_cases = tuple(c.case_id for c in cases)
        cleared: set[str] = set()
        while True:
            await workflow.wait_condition(
                lambda: bool(self._signals) or self._cancel is not None)
            if self._cancel is not None:
                await self._cancelled()
                return False
            signal = self._signals.pop(0)
            pending = tuple(c for c in self._open_cases if c not in cleared)
            verdicts = await self._decide(signal, pending)
            cleared.update(v.case_id for v in verdicts if v.allowed)
            summary = {"signal": type(signal).__name__, "actor": signal.actor,
                       "role": signal.role, "evidence_keys": sorted(signal.evidence),
                       "verdicts": [{"case_id": v.case_id, "allowed": v.allowed, "code": v.code,
                                     "case_status": v.case_status,
                                     "missing_evidence": list(v.missing_evidence)}
                                    for v in verdicts]}
            closed = [v for v in verdicts if not v.allowed
                      and v.case_status in ("REJECTED", "WITHDRAWN")]
            if closed:
                target = (PipelineState.REJECTED if held_from is PipelineState.QA
                          else PipelineState.CANCELLED)
                await self._move(target, outputs=summary, final=True,
                                 error_code="hold_closed_without_clearance",
                                 error_detail=closed[0].message,
                                 hold_case_ids=tuple(v.case_id for v in closed))
                self._open_cases = ()
                return False
            if all(case in cleared for case in self._open_cases):
                await self._move(held_from, outputs=summary, hold_case_ids=self._open_cases)
                self._open_cases = ()
                if self._signals:
                    workflow.logger.warning("%d signal(s) after the hold cleared ignored",
                                            len(self._signals))
                    self._signals.clear()
                return True
            refusals = [v for v in verdicts if not v.allowed]
            await self._move(PipelineState.HELD, outputs=summary, held_from=held_from,
                             hold_case_ids=tuple(v.case_id for v in refusals),
                             error_code=refusals[0].code if refusals else None,
                             error_detail=refusals[0].message if refusals else None)

    async def _decide(self, signal: ResumeSignal | ExternalResolutionSignal,
                      case_ids: tuple[str, ...]) -> list[ResumeVerdict]:
        if isinstance(signal, ExternalResolutionSignal):
            return await self._platform(
                PlatformActivity.RECORD_EXTERNAL_RESOLUTION,
                ExternalResolutionRequest(case_ids=case_ids, actor=signal.actor,
                                          role=signal.role, outcome=signal.outcome,
                                          evidence=dict(signal.evidence), notes=signal.notes,
                                          requested_at=workflow.now()),
                list[ResumeVerdict])
        return await self._platform(
            PlatformActivity.AUTHORIZE_RESUME,
            ResumeRequest(case_ids=case_ids, actor=signal.actor, role=signal.role,
                          evidence=dict(signal.evidence), requested_at=workflow.now()),
            list[ResumeVerdict])

    # -- endings ---------------------------------------------------------------------------

    async def _cancelled(self) -> bool:
        if self._cancel is None:
            return False
        if self._state is not PipelineState.CANCELLED:
            await self._move(PipelineState.CANCELLED, final=True, error_code="cancelled",
                             error_detail=self._cancel.reason,
                             outputs={"actor": self._cancel.actor})
            self._open_cases = ()
        return True

    async def _fail(self, step: Step, failure: _Failure, started: datetime) -> None:
        await self._move(PipelineState.FAILED, step=step, final=True, started_at=started,
                         error_code=str(failure.details.get("code") or failure.type),
                         error_detail=failure.message, attempt=failure.attempt,
                         outputs={"failure": {"type": failure.type,
                                              "non_retryable": failure.non_retryable,
                                              "details": failure.details}})

    async def _fail_fact(self, step: Step, done: _Done, code: str,
                         detail: str) -> PipelineOutcome:
        await self._move(PipelineState.FAILED, step=step, done=done, final=True,
                         error_code=code, error_detail=detail)
        return self._outcome()

    def _outcome(self, *, completed: bool = False) -> PipelineOutcome:
        return PipelineOutcome(
            workflow_id=workflow.info().workflow_id,
            final_state=PipelineState.IDEA.value if self._state is None else self._state.value,
            completed=completed, sequence=self._sequence, error_code=self._last_error,
        )

    # -- persistence -----------------------------------------------------------------------

    async def _move(self, target: PipelineState, *, step: Step | None = None,
                    done: _Done | None = None, outputs: dict[str, Any] | None = None,
                    inputs: dict[str, Any] | None = None, error_code: str | None = None,
                    error_detail: str | None = None, hold_case_ids: tuple[str, ...] = (),
                    held_from: PipelineState | None = None, started_at: datetime | None = None,
                    attempt: int = 0, final: bool = False) -> None:
        assert self._input is not None
        workflow_id = workflow.info().workflow_id
        result = None if done is None else done.result
        settled = None if done is None else done.settled
        if settled is not None:
            cost, basis = settled.actual, settled.basis
        elif result is not None and result.cost is not None:
            cost, basis = result.cost, "provider_reported"
        else:
            cost, basis = None, None
        sequence = self._sequence + 1
        record = TransitionRecord(
            workflow_id=workflow_id, sequence=sequence,
            from_state=None if self._state is None else self._state.value,
            to_state=target.value, occurred_at=workflow.now(),
            directive_id=self._input.directive_id, brand_id=self._input.brand_id,
            step=None if step is None else step.value,
            idempotency_key=None if step is None else idempotency_key(workflow_id, step.value),
            attempt=result.attempt if result is not None else attempt,
            error_code=error_code, error_detail=error_detail,
            started_at=done.started_at if done is not None else started_at,
            provider_ids={} if result is None else dict(result.provider_ids),
            cost=cost, cost_basis=basis,
            inputs=dict(inputs) if inputs is not None
            else ({} if done is None else dict(done.inputs)),
            outputs=dict(outputs) if outputs is not None
            else ({} if result is None else dict(result.outputs)),
            hold_case_ids=hold_case_ids,
            held_from=None if held_from is None else held_from.value, final=final,
        )
        await self._platform(PlatformActivity.RECORD_TRANSITION, record, TransitionAck)
        self._sequence = sequence
        self._state = target
        self._held_from = held_from if target is PipelineState.HELD else None
        if error_code is not None:
            self._last_error = error_code

    async def _platform[R](self, name: PlatformActivity, request: Any,
                           result_type: type[R]) -> R:
        assert self._input is not None
        options = self._input.options
        result: R = await workflow.execute_activity(
            name.value, request, result_type=result_type,
            start_to_close_timeout=timedelta(seconds=options.platform_start_to_close_seconds),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1), backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=options.platform_maximum_attempts,
                non_retryable_error_types=list(NON_RETRYABLE_ERROR_TYPES),
            ),
        )
        return result

    # -- facts -----------------------------------------------------------------------------

    @staticmethod
    def _horizon(value: Any) -> ObservationWindow | None:
        try:
            return ObservationWindow(value)
        except ValueError:
            return None

    @staticmethod
    def _instant(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
        return moment if moment.tzinfo is not None and moment.utcoffset() is not None else None

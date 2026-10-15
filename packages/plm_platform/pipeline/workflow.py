"""The PART XIV pipeline as one durable Temporal workflow per P2 directive (R3–R5).

Workflow id: ``business_workflow_id("pipeline", directive_id)``. The workflow walks the
P27 lifecycle through the activities in :mod:`.activities` and records every transition
in ``ops`` through the ``record_transition`` activity (sequence numbers are assigned
here, deterministically; the store makes each write idempotent).

* **Steps.** One activity per phase step, each called with
  ``idempotency_key(workflow_id, step)`` (a playlist top-up round ``n`` uses
  ``<step>.r<n>``). Retries come from the Temporal retry policy; a non-retryable failure
  ends the step. ``CapabilityUnavailable`` (the runtime cannot do the step) becomes a
  ``CAPABILITY_UNAVAILABLE`` hold; any other permanent failure, or retries exhausted,
  moves the run to ``FAILED`` with the error recorded.
* **Budget (R5).** The pipeline reserves nothing. The phase's external-write guard is
  the single reservation at the write boundary (P4 generation, P7 upload, P8
  submission). A guard refusal (a ``409`` with the guard's reason) raises a reviewable
  hold: ``BUDGET_RESERVATION_REFUSED`` for a budget reason, ``EXTERNAL_WRITE_NOT_AUTHORIZED``
  for a missing authorization or a non-live policy. A resume retries the step with the
  same key, so the guard reserves again against the new headroom and a write is never
  reserved twice. The cost a step records is what the phase reports; the run's spend is
  read from the ledger entries of the writes the guards report (``write_costs``).
* **Playlist supply (R5, owner decision 2026-10-15).** A playlist is accepted anywhere in
  its declared duration range and is never published short. When P6 reports a package's
  cleared supply short of the minimum, the run tops it up inside ``ASSEMBLING``: P3
  ``extend`` adds ``ceil(missing / shortest track) + spare`` track specs, then
  ``generate``, ``qa`` and ``assemble`` run again. At most
  ``PipelineOptions.max_topup_rounds`` rounds; then (or when P3's own extension bound is
  reached) the run holds with ``PLAYLIST_SUPPLY_SHORT``, which a production owner
  resumes with evidence, allowing the same number of rounds again.
* **Holds.** A QA quarantine raises the case's holds and enters ``HELD``. The run leaves
  ``HELD`` only when every hold of the episode is cleared: ``resume(actor, role,
  evidence)`` runs ``authorize_resume`` in an activity (a refusal is recorded, the run
  stays ``HELD``); ``external_resolution(...)`` records a hard stop's external evidence.
  A case closed without clearance ends the run (``REJECTED`` from QA, else
  ``CANCELLED``).
* **Outcome.** ``ANALYZING`` waits on a durable timer until the directive's outcome
  horizon has elapsed after publication (``published_at + outcome_horizon``); a
  ``lifetime`` horizon waits for ``outcome_window_closed``. ``LEARNING`` then evaluates
  the outcome from measured facts, dispatches the reward through P9's outbox, and has P2
  update its posterior and run the next allocation cycle.

Deterministic: time is ``workflow.now()`` (every instant a step sends to a phase is the
request's ``requested_at``), there is no randomness, and only the sandbox-safe
vocabulary modules are imported (passed through). Nothing assumes wall-clock time
elapsed between two steps, so a time-skipping test server runs the same code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
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
        STEP_ORIGIN_PHASE,
        PipelineState,
        PlatformActivity,
        Step,
        step_activity_name,
    )
    from .types import (
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
        StepRequest,
        StepResult,
        TransitionAck,
        TransitionRecord,
        WriteCost,
        WriteCostsRequest,
    )

__all__ = [
    "BUDGET_HOLD_REASON",
    "CAPABILITY_HOLD_REASON",
    "SUPPLY_HOLD_REASON",
    "WORKFLOW_NAME",
    "PipelineWorkflow",
    "topup_count",
]

WORKFLOW_NAME: Final[str] = "PipelineWorkflow"
BUDGET_HOLD_REASON: Final[str] = "BUDGET_RESERVATION_REFUSED"
CAPABILITY_HOLD_REASON: Final[str] = "CAPABILITY_UNAVAILABLE"
"""Classified HARD_STOP in the hold policy: no approval can produce the missing work."""
SUPPLY_HOLD_REASON: Final[str] = "PLAYLIST_SUPPLY_SHORT"
UNCLASSIFIED_WRITE_HOLD_REASON: Final[str] = "EXTERNAL_WRITE_REFUSED"
SUPPLY_SHORT_FAILURE: Final[str] = "PlaylistSupplyShort"
EXTENSION_LIMIT_CODE: Final[str] = "package_extension_limit_exceeded"


def topup_count(missing_seconds: int, shortest_track_seconds: int, spare: int) -> int:
    """Track specs a top-up asks for: enough of the shortest declared track to cover the
    missing seconds, plus the declared spare for P5 rejections."""
    if missing_seconds <= 0 or shortest_track_seconds <= 0 or spare < 0:
        raise ValueError("a top-up needs positive missing and track seconds, spare >= 0")
    return math.ceil(missing_seconds / shortest_track_seconds) + spare


@dataclass(frozen=True, slots=True)
class _Failure:
    type: str
    message: str
    attempt: int
    non_retryable: bool
    details: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    key_name: str | None = None

    @property
    def no_effect(self) -> bool:
        return self.details.get("no_effect") is True

    @property
    def code(self) -> str | None:
        code = self.details.get("code")
        return code if isinstance(code, str) else None


@dataclass(frozen=True, slots=True)
class _Done:
    result: StepResult
    started_at: datetime
    inputs: dict[str, Any] = field(default_factory=dict)
    """What the step was given (references to earlier steps' outputs)."""
    key_name: str | None = None
    """The idempotency-key name when it is not the step name (a top-up round)."""


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


def _cost_dict(cost: WriteCost) -> dict[str, Any]:
    return {"capability": cost.capability, "idempotency_key": cost.idempotency_key,
            "decision": cost.decision, "status": cost.status,
            "actual": None if cost.actual is None
            else {"amount": cost.actual.amount, "currency": cost.actual.currency},
            "basis": cost.basis,
            "settled_at": None if cost.settled_at is None else cost.settled_at.isoformat(),
            "context": dict(cost.context)}


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
        self._receipts: list[dict[str, Any]] = []
        """Every external write the phases reported, for the cost read at the outcome."""

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

        generated = await self._step(Step.GENERATE, {"policy_check": policy.result.outputs},
                                     enter=PipelineState.GENERATING)
        if generated is None:
            return self._outcome()
        self._collect_receipts(generated)
        await self._move(PipelineState.GENERATED, step=Step.GENERATE, done=generated)

        if not await self._qa(generated):
            return self._outcome()

        assembled = await self._assemble(policy, generated)
        if assembled is None:
            return self._outcome()
        await self._move(PipelineState.PACKAGED, step=Step.ASSEMBLE, done=assembled)

        uploaded = await self._step(Step.UPLOAD, {"assemble": assembled.result.outputs},
                                    enter=PipelineState.UPLOADING)
        if uploaded is None:
            return self._outcome()
        self._collect_receipts(uploaded)
        await self._move(PipelineState.UPLOADING, step=Step.UPLOAD, done=uploaded)

        submitted = await self._step(
            Step.SUBMIT_DISTRIBUTION,
            {"assemble": assembled.result.outputs, "upload": uploaded.result.outputs},
            enter=PipelineState.UPLOADING)
        if submitted is None:
            return self._outcome()
        self._collect_receipts(submitted)
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

        costs = await self._platform(
            PlatformActivity.WRITE_COSTS,
            WriteCostsRequest(receipts=tuple(self._receipts), requested_at=workflow.now()),
            list[WriteCost])
        outcome_inputs = {
            "research": research.result.outputs, "assemble": assembled.result.outputs,
            "upload": uploaded.result.outputs, "submit_distribution": submitted.result.outputs,
            "await_processing": processed.result.outputs,
            "published_at": published_at.isoformat(), "outcome_horizon": horizon.value,
            "costs": [_cost_dict(c) for c in costs],
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
        await self._move(PipelineState.LEARNING, step=Step.DISPATCH_REWARD, done=dispatched)

        learned = await self._step(Step.LEARN, {"dispatch_reward": dispatched.result.outputs,
                                                "evaluate_outcome": evaluated.result.outputs})
        if learned is None:
            return self._outcome()
        await self._move(PipelineState.LEARNING, step=Step.LEARN, done=learned, final=True)
        return self._outcome(completed=True)

    # -- QA, the playlist supply and the outcome horizon -----------------------------------

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

    async def _assemble(self, policy: _Done, generated: _Done) -> _Done | None:
        """Assemble every package; top a short playlist up, bounded, then hold."""
        assert self._input is not None
        options = self._input.options
        candidates = [str(c) for c in generated.result.outputs.get("candidate_ids") or []]
        rounds = 0
        allowance = options.max_topup_rounds
        await self._move(PipelineState.ASSEMBLING)
        while True:
            key_name = Step.ASSEMBLE.value if rounds == 0 else f"{Step.ASSEMBLE.value}.r{rounds}"
            inputs = {"policy_check": policy.result.outputs,
                      "generate": {"candidate_ids": list(candidates)}}
            outcome = await self._attempt(Step.ASSEMBLE, inputs, key_name=key_name)
            if outcome is None:
                return None
            if isinstance(outcome, _Done):
                return outcome
            if outcome.type != SUPPLY_SHORT_FAILURE:
                await self._fail(Step.ASSEMBLE, outcome)
                return None
            shortages = [s for s in outcome.details.get("short_packages") or []
                         if isinstance(s, dict)]
            await self._move(PipelineState.ASSEMBLING, step=Step.ASSEMBLE, key_name=key_name,
                             started_at=outcome.started_at, attempt=outcome.attempt,
                             inputs=inputs, error_code="playlist_supply_short",
                             error_detail=outcome.message,
                             outputs={"short_packages": shortages,
                                      "topup_rounds_done": rounds,
                                      "topup_rounds_left": allowance})
            if not shortages:
                await self._fail(Step.ASSEMBLE, outcome)
                return None
            while True:
                if allowance <= 0:
                    if not await self._supply_hold(shortages, rounds, "topup_rounds_exhausted"):
                        return None
                    allowance = options.max_topup_rounds
                rounds += 1
                allowance -= 1
                added = await self._top_up(rounds, shortages)
                if isinstance(added, list):
                    candidates.extend(c for c in added if c not in candidates)
                    break
                if added is None:
                    return None
                # P3 refused to extend further: hold now, whatever rounds were left.
                allowance = 0

    async def _top_up(self, round_no: int,
                      shortages: list[dict[str, Any]]) -> list[str] | str | None:
        """One round: extend each short package, generate the new specs, QA them.

        Returns the new candidate ids; ``"extension_limit"`` when P3 refused to add more
        specs (its declared bound); ``None`` when the run ended."""
        assert self._input is not None
        spare = self._input.options.topup_spare_tracks
        spec_ids: list[str] = []
        packages: list[str] = []
        for index, shortage in enumerate(shortages):
            package_id = str(shortage.get("p3_package_id"))
            missing = shortage.get("missing_seconds")
            shortest = shortage.get("shortest_track_seconds")
            if not isinstance(missing, int) or not isinstance(shortest, int) or (
                    missing <= 0 or shortest <= 0):
                failure = _Failure(type="ValidationFailure", attempt=0, non_retryable=True,
                                   message=f"short package {package_id}: missing {missing!r} "
                                           f"or shortest track {shortest!r} is unusable")
                await self._fail(Step.ASSEMBLE, failure)
                return None
            inputs = {"p3_package_id": package_id,
                      "count": topup_count(missing, shortest, spare)}
            extended = await self._attempt(Step.EXTEND, inputs,
                                           key_name=f"{Step.EXTEND.value}.r{round_no}.{index}")
            if extended is None:
                return None
            if isinstance(extended, _Failure):
                if extended.code == EXTENSION_LIMIT_CODE:
                    await self._move(PipelineState.ASSEMBLING, step=Step.EXTEND,
                                     key_name=extended.key_name, inputs=inputs,
                                     started_at=extended.started_at, attempt=extended.attempt,
                                     error_code=EXTENSION_LIMIT_CODE,
                                     error_detail=extended.message)
                    return "extension_limit"
                await self._fail(Step.EXTEND, extended)
                return None
            await self._move(PipelineState.ASSEMBLING, step=Step.EXTEND, done=extended)
            spec_ids.extend(str(s) for s in extended.result.outputs.get("track_spec_ids") or [])
            packages.append(package_id)
        generated = await self._step(
            Step.GENERATE, {"policy_check": {"package_ids": packages},
                            "track_spec_ids": spec_ids},
            key_name=f"{Step.GENERATE.value}.r{round_no}")
        if generated is None:
            return None
        self._collect_receipts(generated)
        await self._move(PipelineState.ASSEMBLING, step=Step.GENERATE, done=generated)
        checked = await self._step(Step.QA, {"generate": generated.result.outputs},
                                   key_name=f"{Step.QA.value}.r{round_no}")
        if checked is None:
            return None
        # A top-up candidate P5 quarantines or rejects simply adds no supply; its case
        # stays open in P5 and the next assemble offers P6 only cleared recordings.
        await self._move(PipelineState.ASSEMBLING, step=Step.QA, done=checked)
        return [str(c) for c in generated.result.outputs.get("candidate_ids") or []]

    async def _supply_hold(self, shortages: list[dict[str, Any]], rounds: int,
                           why: str) -> bool:
        details = {"short_packages": shortages, "topup_rounds_done": rounds, "why": why,
                   "rule": "a playlist is never published short of its declared minimum"}
        return await self._hold(
            Step.ASSEMBLE, [(STEP_ORIGIN_PHASE[Step.ASSEMBLE], SUPPLY_HOLD_REASON, details)],
            error_code="playlist_supply_short",
            error_detail=f"cleared supply still short after {rounds} top-up round(s) ({why})")

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

    def _request(self, step: Step, inputs: dict[str, Any], key_name: str) -> StepRequest:
        assert self._input is not None
        workflow_id = workflow.info().workflow_id
        return StepRequest(
            workflow_id=workflow_id, directive_id=self._input.directive_id,
            brand_id=self._input.brand_id, step=step.value,
            idempotency_key=idempotency_key(workflow_id, key_name),
            requested_at=workflow.now(), inputs=inputs,
        )

    async def _call(self, step: Step, inputs: dict[str, Any],
                    key_name: str) -> StepResult | _Failure:
        assert self._input is not None
        options = self._input.options
        try:
            result: StepResult = await workflow.execute_activity(
                step_activity_name(step), self._request(step, inputs, key_name),
                result_type=StepResult,
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

    async def _attempt(self, step: Step, inputs: dict[str, Any], *,
                       enter: PipelineState | None = None,
                       key_name: str | None = None) -> _Done | _Failure | None:
        """Run a step until it returns, or fails with a failure no hold resolves.

        A capability gap or a write-guard refusal holds the run and, once every hold is
        cleared, retries the step with the same key. ``None``: the run ended (cancelled
        or a hold closed without clearance)."""
        name = step.value if key_name is None else key_name
        while True:
            if await self._cancelled():
                return None
            if enter is not None and self._state is not enter:
                await self._move(enter)
            started = workflow.now()
            outcome = await self._call(step, inputs, name)
            if isinstance(outcome, StepResult):
                return _Done(result=outcome, started_at=started, inputs=inputs,
                             key_name=None if name == step.value else name)
            failure = replace(outcome, started_at=started, key_name=name)
            if failure.type == "CapabilityUnavailable":
                if await self._capability_hold(step, failure):
                    continue
                return None
            if failure.type == "ExternalWriteRefused":
                if await self._write_hold(step, failure):
                    continue
                return None
            return failure

    async def _step(self, step: Step, inputs: dict[str, Any], *,
                    enter: PipelineState | None = None,
                    key_name: str | None = None) -> _Done | None:
        """Run a step; ``None`` means the run ended (failed, cancelled, closed)."""
        outcome = await self._attempt(step, inputs, enter=enter, key_name=key_name)
        if isinstance(outcome, _Failure):
            await self._fail(step, outcome)
            return None
        return outcome

    def _collect_receipts(self, done: _Done) -> None:
        writes = done.result.outputs.get("external_writes")
        if isinstance(writes, list):
            self._receipts.extend(w for w in writes if isinstance(w, dict))

    async def _capability_hold(self, step: Step, failure: _Failure) -> bool:
        details = {"step": step.value, "error": failure.message,
                   "owning_stage": failure.details.get("owning_stage"),
                   "capability": failure.details.get("capability")}
        return await self._hold(step, [(STEP_ORIGIN_PHASE[step], CAPABILITY_HOLD_REASON, details)],
                                error_code=CAPABILITY_HOLD_REASON, error_detail=failure.message,
                                attempt=failure.attempt, key_name=failure.key_name)

    async def _write_hold(self, step: Step, failure: _Failure) -> bool:
        """A phase's write guard refused: hold for the reviewer its reason calls for."""
        reason_code = failure.details.get("hold_reason_code")
        if not isinstance(reason_code, str) or not reason_code:
            reason_code = UNCLASSIFIED_WRITE_HOLD_REASON
        details = {"step": step.value, "error": failure.message,
                   "idempotency_key": idempotency_key(workflow.info().workflow_id,
                                                      failure.key_name or step.value)}
        for name in ("capability", "guard_decision", "guard_reason", "ledger_reason",
                     "refusing_phase"):
            if failure.details.get(name) is not None:
                details[name] = failure.details[name]
        guard_reason = failure.details.get("guard_reason")
        return await self._hold(
            step, [(STEP_ORIGIN_PHASE[step], reason_code, details)],
            error_code=guard_reason if isinstance(guard_reason, str) else failure.code
            or reason_code,
            error_detail=failure.message, attempt=failure.attempt, key_name=failure.key_name)

    # -- holds -----------------------------------------------------------------------------

    async def _hold(self, step: Step, specs: list[tuple[str, str, dict[str, Any]]], *,
                    error_code: str, error_detail: str | None = None,
                    done: _Done | None = None, attempt: int = 0,
                    key_name: str | None = None) -> bool:
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
                         attempt=attempt, key_name=key_name)
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

    async def _fail(self, step: Step, failure: _Failure) -> None:
        if self._state in (PipelineState.FAILED, PipelineState.CANCELLED,
                           PipelineState.REJECTED):
            return
        await self._move(PipelineState.FAILED, step=step, final=True,
                         started_at=failure.started_at, key_name=failure.key_name,
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
                    attempt: int = 0, final: bool = False,
                    key_name: str | None = None) -> None:
        assert self._input is not None
        workflow_id = workflow.info().workflow_id
        result = None if done is None else done.result
        if result is not None and result.cost is not None:
            cost, basis = result.cost, result.cost_basis or "provider_reported"
        else:
            cost, basis = None, None
        if done is not None and done.key_name is not None:
            key_name = done.key_name
        sequence = self._sequence + 1
        record = TransitionRecord(
            workflow_id=workflow_id, sequence=sequence,
            from_state=None if self._state is None else self._state.value,
            to_state=target.value, occurred_at=workflow.now(),
            directive_id=self._input.directive_id, brand_id=self._input.brand_id,
            step=None if step is None else step.value,
            idempotency_key=None if step is None
            else idempotency_key(workflow_id, key_name or step.value),
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

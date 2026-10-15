"""The Exceptions view's actions: resume, reject and record an external resolution (R5).

Every decision goes through :class:`~plm_platform.exceptions.ExceptionQueue`, so the hold
class rules (ADR-0033, architecture PART XVIII) are the queue's, not the dashboard's:

* **Resume** calls :meth:`ExceptionQueue.resolve` with ``RESUME``, which runs the same
  check as :meth:`ExceptionQueue.authorize_resume` (a hard stop is refused for every
  role; a reviewable hold needs the policy's reviewer role, an actor the reviewer
  directory confirms holds it, and every required evidence item) and records a refusal
  as a case event before it is returned with its code.
* **Record external resolution** calls :meth:`ExceptionQueue.record_external_resolution`
  (hard stops only, with the external process's evidence).
* **Reject** calls :meth:`ExceptionQueue.resolve` with ``REJECT``. The queue only needs an
  identified actor to reject; the dashboard additionally requires that the actor holds
  the case's reviewer role in the directory, so an unknown identity cannot close a hold.
  That stricter refusal is returned with its code but is not a case event (the queue has
  no refusal record for a reject).

The acting identity comes from the request and is authorized against the configured
:class:`~plm_platform.exceptions.StaticReviewerDirectory` (``PLM_DASHBOARD_REVIEWERS_FILE``).
With no directory configured every action is refused (``reviewer_directory_not_configured``);
nothing is open by default.

**Pipeline signal.** A hold raised by the pipeline workflow (``raised_by =
"pipeline:<workflow_id>"``, ``subject_ref = "<workflow_id>:<run_id>:<step>:<episode>"``)
is decided here first and committed; the workflow is then signalled so it re-checks its
holds through its own ``authorize_resume`` activity:

* resume → the ``resume`` signal; external resolution → ``external_resolution``;
  reject → ``resume`` (the workflow sees the closed case and ends the run: ``REJECTED``
  when held from QA, else ``CANCELLED``);
* while another hold of the same episode is still open, a clearing decision does not
  signal yet (``deferred``): a signal would ask the workflow to evaluate the other open
  holds with evidence given for this one;
* if Temporal is not configured or does not answer, the decision stays recorded and the
  signal is reported ``pending`` with the reason. :meth:`ExceptionActions.resend_signal`
  sends it later; the Production Queue shows HELD runs whose holds are all closed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, Protocol

import sqlalchemy as sa
from plm_contracts import ErrorShape
from sqlalchemy.orm import Session

from ..clock import Clock, SystemClock
from ..config import Settings
from ..exceptions import (
    CaseStatus,
    DecisionCode,
    ExceptionCase,
    ExceptionQueue,
    ExternalOutcome,
    ExternalResolutionRefusedError,
    HoldClass,
    ResumeDecision,
    ResumeRefusedError,
    ReviewDecision,
    StaticReviewerDirectory,
    exception_cases_table,
)
from ..pipeline.store import pipeline_runs
from ..pipeline.types import ExternalResolutionSignal, ResumeSignal

__all__ = [
    "REVIEWERS_FILE_VAR",
    "ActionRefused",
    "ExceptionActions",
    "PipelineLink",
    "PipelineSignaller",
    "ReviewerConfig",
    "SignalOutcome",
    "SignalRequest",
    "TemporalSignaller",
    "allowed_actions",
    "pipeline_link",
]

REVIEWERS_FILE_VAR: Final[str] = "PLM_DASHBOARD_REVIEWERS_FILE"
PIPELINE_RAISER_PREFIX: Final[str] = "pipeline:"

SignalName = Literal["resume", "external_resolution"]


# --------------------------------------------------------------------------- reviewers


@dataclass(frozen=True, slots=True)
class ReviewerConfig:
    """The reviewer directory the dashboard authorizes actors against."""

    grants: Mapping[str, frozenset[str]]
    source: str

    @property
    def directory(self) -> StaticReviewerDirectory:
        return StaticReviewerDirectory(self.grants)

    def roles_of(self, actor: str) -> frozenset[str]:
        return self.grants.get(actor, frozenset())

    @classmethod
    def from_json_file(cls, path: str | Path) -> ReviewerConfig:
        """``{"actor": ["role", ...]}``; validated exactly as the queue's directory is."""
        StaticReviewerDirectory.from_json_file(path)  # the queue's own validation
        raw: dict[str, list[str]] = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(grants={actor: frozenset(roles) for actor, roles in raw.items()},
                   source=str(path))

    def describe(self) -> list[dict[str, Any]]:
        return [{"actor": actor, "roles": sorted(roles)}
                for actor, roles in sorted(self.grants.items())]


# ---------------------------------------------------------------------------- pipeline


@dataclass(frozen=True, slots=True)
class PipelineLink:
    """The pipeline run a hold belongs to, parsed from the case's stored identifiers."""

    workflow_id: str
    run_id: str
    step: str
    episode: str


def pipeline_link(case: ExceptionCase) -> PipelineLink | None:
    """``None`` unless the pipeline workflow raised this hold (see module docstring)."""
    if not case.raised_by.startswith(PIPELINE_RAISER_PREFIX):
        return None
    workflow_id = case.raised_by[len(PIPELINE_RAISER_PREFIX):]
    parts = case.subject_ref.split(":")
    if len(parts) != 4 or parts[0] != workflow_id or not all(parts):
        return None
    return PipelineLink(workflow_id=workflow_id, run_id=parts[1], step=parts[2],
                        episode=parts[3])


def allowed_actions(case: ExceptionCase) -> list[str]:
    """What the UI may offer for an open case; the queue still decides every request."""
    if not case.is_open:
        return []
    if case.hold_class is HoldClass.HARD_STOP:
        return ["record_external_resolution", "reject"]
    if case.override_permitted:
        return ["resume", "reject"]
    return ["reject"]


@dataclass(frozen=True, slots=True)
class SignalRequest:
    workflow_id: str
    run_id: str
    signal: SignalName
    payload: ResumeSignal | ExternalResolutionSignal


class PipelineSignaller(Protocol):
    """Delivers one signal to one pipeline run; raises when it cannot."""

    async def signal(self, request: SignalRequest) -> None: ...


class TemporalSignaller:
    """Signals the pipeline workflow on the configured Temporal server.

    Connects per call (a dashboard action is rare) so no client outlives the event loop
    that created it; ``timeout_seconds`` bounds connect and signal together.
    """

    def __init__(self, settings: Settings, *, timeout_seconds: float = 5.0) -> None:
        self._settings = settings
        self._timeout = timeout_seconds

    async def signal(self, request: SignalRequest) -> None:
        from ..pipeline.workflow import PipelineWorkflow
        from ..temporal import connect_client

        async with asyncio.timeout(self._timeout):
            client = await connect_client(self._settings)
            handle = client.get_workflow_handle(request.workflow_id, run_id=request.run_id)
            if isinstance(request.payload, ExternalResolutionSignal):
                await handle.signal(PipelineWorkflow.external_resolution, request.payload)
            else:
                await handle.signal(PipelineWorkflow.resume, request.payload)


@dataclass(frozen=True, slots=True)
class SignalOutcome:
    """``sent`` | ``pending`` (decision recorded, signal not delivered) | ``deferred``
    (other holds of the episode still open) | ``not_applicable`` (not a pipeline hold)."""

    status: Literal["sent", "pending", "deferred", "not_applicable"]
    signal: str | None = None
    workflow_id: str | None = None
    run_id: str | None = None
    reason: str | None = None
    open_case_ids: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["open_case_ids"] = list(self.open_case_ids)
        return body


# ---------------------------------------------------------------------------- refusals


class ActionRefused(Exception):
    """An action the dashboard refuses, with the HTTP status and ErrorShape to return."""

    def __init__(self, status_code: int, code: str, message: str,
                 field_paths: list[str] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.error = ErrorShape(code=code, field_paths=field_paths or [], message=message)


def _refused(decision: ResumeDecision) -> ActionRefused:
    paths = [f"evidence.{name}" for name in decision.missing_evidence]
    if decision.code in (DecisionCode.REVIEWER_ROLE_MISMATCH, DecisionCode.REVIEWER_NOT_AUTHORIZED):
        paths = ["role"]
    elif decision.code is DecisionCode.ACTOR_MISSING:
        paths = ["actor"]
    return ActionRefused(409, decision.code.value, decision.message, paths)


# ----------------------------------------------------------------------------- actions


class ExceptionActions:
    """Drive the R3 exception queue from the dashboard (see module docstring)."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | None,
        *,
        reviewers: ReviewerConfig | None,
        signaller: PipelineSignaller | None,
        clock: Clock | None = None,
        signaller_missing_reason: str = "temporal not configured (TEMPORAL_HOST)",
    ) -> None:
        self._factory = session_factory
        self._reviewers = reviewers
        self._signaller = signaller
        self._signaller_missing = signaller_missing_reason
        self._clock = clock if clock is not None else SystemClock()
        self._queue = ExceptionQueue(
            reviewers=reviewers.directory if reviewers is not None else StaticReviewerDirectory({}))

    @property
    def reviewers(self) -> ReviewerConfig | None:
        return self._reviewers

    # -- public actions -------------------------------------------------------------------

    async def resume(self, case_id: str, *, actor: str, role: str | None,
                     evidence: Mapping[str, str], notes: str) -> dict[str, Any]:
        def decide(session: Session, case: ExceptionCase) -> ExceptionCase:
            try:
                return self._queue.resolve(
                    session, case.id, decision=ReviewDecision.RESUME, actor=actor, role=role,
                    evidence=evidence, notes=notes or "resumed from the dashboard",
                    now=self._clock.now())
            except ResumeRefusedError as refused:
                session.commit()  # the refusal is a case event; keep it
                raise _refused(refused.decision) from None

        case = await asyncio.to_thread(self._decide, case_id, decide)
        signal = await self._signal_after(case, ResumeSignal(
            actor=actor, role=role, evidence=dict(evidence)), "resume")
        return self._answer(case, signal)

    async def external_resolution(self, case_id: str, *, actor: str, role: str | None,
                                  outcome: str, evidence: Mapping[str, str],
                                  notes: str) -> dict[str, Any]:
        try:
            resolved_outcome = ExternalOutcome(outcome)
        except ValueError:
            raise ActionRefused(
                422, "invalid_outcome",
                f"outcome must be one of {[o.value for o in ExternalOutcome]}", ["outcome"],
            ) from None

        def decide(session: Session, case: ExceptionCase) -> ExceptionCase:
            try:
                return self._queue.record_external_resolution(
                    session, case.id, outcome=resolved_outcome, evidence=evidence,
                    actor=actor, role=role, notes=notes, now=self._clock.now())
            except ExternalResolutionRefusedError as refused:
                session.commit()
                raise _refused(refused.decision) from None

        case = await asyncio.to_thread(self._decide, case_id, decide)
        signal = await self._signal_after(case, ExternalResolutionSignal(
            actor=actor, role=role, evidence=dict(evidence), outcome=resolved_outcome.value,
            notes=notes), "external_resolution")
        return self._answer(case, signal)

    async def reject(self, case_id: str, *, actor: str, role: str | None,
                     notes: str) -> dict[str, Any]:
        def decide(session: Session, case: ExceptionCase) -> ExceptionCase:
            if not case.is_open:
                raise ActionRefused(409, DecisionCode.CASE_CLOSED.value,
                                    f"case is {case.status.value}")
            self._require_reviewer(case, actor, role)
            try:
                return self._queue.resolve(
                    session, case.id, decision=ReviewDecision.REJECT, actor=actor, role=role,
                    evidence={}, notes=notes or "rejected from the dashboard",
                    now=self._clock.now())
            except ResumeRefusedError as refused:
                session.commit()
                raise _refused(refused.decision) from None

        case = await asyncio.to_thread(self._decide, case_id, decide)
        signal = await self._signal_after(case, ResumeSignal(actor=actor, role=role), "resume")
        return self._answer(case, signal)

    async def resend_signal(self, workflow_id: str, *, actor: str) -> dict[str, Any]:
        """Deliver the pending signal of a HELD run whose holds are all closed."""
        reviewers = self._require_directory()
        if not actor or not reviewers.roles_of(actor):
            raise ActionRefused(403, DecisionCode.REVIEWER_NOT_AUTHORIZED.value,
                                f"{actor!r} holds no role in the reviewer directory",
                                ["actor"])
        cases = await asyncio.to_thread(self._held_episode, workflow_id)
        open_cases = [c for c in cases if c.is_open]
        if open_cases:
            raise ActionRefused(409, "holds_still_open",
                                f"{len(open_cases)} hold(s) of this run are still open",
                                [f"case_id.{c.id}" for c in open_cases])
        link = pipeline_link(cases[0])
        assert link is not None
        cleared_externally = any(
            c.hold_class is HoldClass.HARD_STOP and c.status is CaseStatus.RESOLVED
            for c in cases)
        role = min(reviewers.roles_of(actor))
        payload: ResumeSignal | ExternalResolutionSignal
        if cleared_externally:
            payload = ExternalResolutionSignal(actor=actor, role=role, evidence={})
            name: SignalName = "external_resolution"
        else:
            payload, name = ResumeSignal(actor=actor, role=role), "resume"
        outcome = await self._deliver(link, name, payload)
        return {"workflow_id": workflow_id, "signal": outcome.as_dict()}

    # -- internals ------------------------------------------------------------------------

    def _require_directory(self) -> ReviewerConfig:
        if self._reviewers is None:
            raise ActionRefused(
                503, "reviewer_directory_not_configured",
                f"no reviewer directory is configured ({REVIEWERS_FILE_VAR}); every "
                "exception action is refused until one is", [REVIEWERS_FILE_VAR])
        return self._reviewers

    def _require_reviewer(self, case: ExceptionCase, actor: str, role: str | None) -> None:
        reviewers = self._require_directory()
        if not actor or not actor.strip():
            raise ActionRefused(409, DecisionCode.ACTOR_MISSING.value,
                                "no reviewer identity given", ["actor"])
        if role != case.reviewer_role:
            raise ActionRefused(409, DecisionCode.REVIEWER_ROLE_MISMATCH.value,
                                f"requires role {case.reviewer_role!r}, request acts as "
                                f"{role!r}", ["role"])
        if case.reviewer_role not in reviewers.roles_of(actor):
            raise ActionRefused(409, DecisionCode.REVIEWER_NOT_AUTHORIZED.value,
                                f"{actor!r} does not hold role {case.reviewer_role!r}",
                                ["role"])

    def _decide(self, case_id: str,
                decide: Callable[[Session, ExceptionCase], ExceptionCase]) -> ExceptionCase:
        self._require_directory()
        if self._factory is None:
            raise ActionRefused(503, "database_unavailable",
                                "database not configured (DATABASE_URL)")
        with self._factory() as session:
            case = self._queue.get(session, case_id)
            if case is None:
                raise ActionRefused(404, "unknown_case", f"no exception case {case_id!r}",
                                    ["case_id"])
            try:
                closed = decide(session, case)
            except ActionRefused:
                raise
            except BaseException:
                session.rollback()
                raise
            session.commit()
            return closed

    def _episode(self, session: Session, case: ExceptionCase) -> list[ExceptionCase]:
        ids = session.execute(
            sa.select(exception_cases_table.c.id)
            .where(exception_cases_table.c.subject_ref == case.subject_ref)
            .order_by(exception_cases_table.c.id)
        ).scalars().all()
        return [self._queue.require(session, case_id) for case_id in ids]

    def _held_episode(self, workflow_id: str) -> list[ExceptionCase]:
        """The cases of the hold episode a HELD run is waiting on."""
        if self._factory is None:
            raise ActionRefused(503, "database_unavailable",
                                "database not configured (DATABASE_URL)")
        with self._factory() as session:
            state = session.execute(
                sa.select(pipeline_runs.c.state)
                .where(pipeline_runs.c.workflow_id == workflow_id)
            ).scalar_one_or_none()
            if state is None:
                raise ActionRefused(404, "unknown_run", f"no pipeline run {workflow_id!r}",
                                    ["workflow_id"])
            if state != "HELD":
                raise ActionRefused(409, "run_not_held", f"run is {state}, not HELD")
            raised = session.execute(
                sa.select(exception_cases_table.c.id, exception_cases_table.c.subject_ref)
                .where(exception_cases_table.c.raised_by
                       == f"{PIPELINE_RAISER_PREFIX}{workflow_id}")
                .order_by(exception_cases_table.c.raised_at.desc(),
                          exception_cases_table.c.id)
            ).all()
            if not raised:
                raise ActionRefused(409, "no_holds_recorded",
                                    "the run is HELD but no hold case names it")
            latest_subject = raised[0].subject_ref
            return [self._queue.require(session, row.id) for row in raised
                    if row.subject_ref == latest_subject]

    async def _signal_after(self, case: ExceptionCase,
                            payload: ResumeSignal | ExternalResolutionSignal,
                            name: SignalName) -> SignalOutcome:
        link = pipeline_link(case)
        if link is None:
            return SignalOutcome(status="not_applicable",
                                 reason="the hold was not raised by a pipeline run")
        cleared = case.status is CaseStatus.RESOLVED
        if cleared:
            siblings = await asyncio.to_thread(self._siblings, case)
            still_open = tuple(c.id for c in siblings if c.is_open and c.id != case.id)
            if still_open:
                return SignalOutcome(
                    status="deferred", signal=name, workflow_id=link.workflow_id,
                    run_id=link.run_id, open_case_ids=still_open,
                    reason="other holds of this run are still open; the run is signalled "
                           "when the last one is decided")
        return await self._deliver(link, name, payload)

    def _siblings(self, case: ExceptionCase) -> list[ExceptionCase]:
        assert self._factory is not None
        with self._factory() as session:
            return self._episode(session, case)

    async def _deliver(self, link: PipelineLink, name: SignalName,
                       payload: ResumeSignal | ExternalResolutionSignal) -> SignalOutcome:
        def outcome(status: Literal["sent", "pending"], reason: str | None) -> SignalOutcome:
            return SignalOutcome(status=status, signal=name, workflow_id=link.workflow_id,
                                 run_id=link.run_id, reason=reason)

        if self._signaller is None:
            return outcome("pending", self._signaller_missing)
        try:
            await self._signaller.signal(SignalRequest(
                workflow_id=link.workflow_id, run_id=link.run_id, signal=name, payload=payload))
        except Exception as exc:  # noqa: BLE001 - any failure leaves the signal pending
            detail = str(exc).strip() or type(exc).__name__
            return outcome("pending", f"{type(exc).__name__}: {detail}"[:300])
        return outcome("sent", None)

    @staticmethod
    def _answer(case: ExceptionCase, signal: SignalOutcome) -> dict[str, Any]:
        return {
            "case": {"case_id": case.id, "status": case.status.value,
                     "resolution": None if case.resolution is None else case.resolution.value,
                     "hold_class": case.hold_class.value, "reason_code": case.reason_code,
                     "closed_by": case.closed_by,
                     "closed_at": None if case.closed_at is None else case.closed_at.isoformat()},
            "signal": signal.as_dict(),
        }

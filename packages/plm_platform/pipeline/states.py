"""Pipeline states, steps and the transition rules (architecture PART XIV, P27).

Pure vocabulary: standard library only, so the Temporal workflow sandbox can import it
and the persistence layer can enforce exactly the rules the workflow follows.

The lifecycle (P27)::

    IDEA -> RESEARCHED -> POLICY_CHECKED -> GENERATING -> GENERATED -> QA
    QA -> PASSED | REJECTED
    PASSED -> ASSEMBLING -> PACKAGED -> UPLOADING -> PROCESSING -> PUBLISHED
    PUBLISHED -> ANALYZING -> LEARNING

plus three runtime states:

* ``HELD``: an exception-queue hold stops the run. It returns only to the state it was
  held from (after an authorized resume or a recorded external resolution), or ends in
  ``REJECTED`` / ``FAILED`` / ``CANCELLED``.
* ``FAILED``: a step failed permanently (non-retryable error, retries exhausted, or a
  required fact was missing).
* ``CANCELLED``: an operator stopped the run, or a hold was closed without clearance.

A self-transition (``X -> X``) records a step that completed without moving the run
(for example the YouTube upload inside ``UPLOADING`` before the distribution
submission), or a refused resume inside ``HELD``. ``REJECTED``, ``FAILED`` and
``CANCELLED`` are terminal. ``LEARNING`` is the last lifecycle state; a run is complete
when its final ``LEARNING`` step is recorded with ``final=True``.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "ALLOWED_TRANSITIONS",
    "LIFECYCLE",
    "PAID_STEP_CAPABILITIES",
    "STEP_ORIGIN_PHASE",
    "TERMINAL_STATES",
    "IllegalTransitionError",
    "PipelineState",
    "PlatformActivity",
    "Step",
    "check_transition",
    "step_activity_name",
]


class PipelineState(StrEnum):
    IDEA = "IDEA"
    RESEARCHED = "RESEARCHED"
    POLICY_CHECKED = "POLICY_CHECKED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    QA = "QA"
    REJECTED = "REJECTED"
    PASSED = "PASSED"
    ASSEMBLING = "ASSEMBLING"
    PACKAGED = "PACKAGED"
    UPLOADING = "UPLOADING"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    ANALYZING = "ANALYZING"
    LEARNING = "LEARNING"
    HELD = "HELD"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


LIFECYCLE: Final[tuple[PipelineState, ...]] = (
    PipelineState.IDEA,
    PipelineState.RESEARCHED,
    PipelineState.POLICY_CHECKED,
    PipelineState.GENERATING,
    PipelineState.GENERATED,
    PipelineState.QA,
    PipelineState.PASSED,
    PipelineState.ASSEMBLING,
    PipelineState.PACKAGED,
    PipelineState.UPLOADING,
    PipelineState.PROCESSING,
    PipelineState.PUBLISHED,
    PipelineState.ANALYZING,
    PipelineState.LEARNING,
)
"""The happy path, in order (``REJECTED`` is QA's other exit)."""

TERMINAL_STATES: Final[frozenset[PipelineState]] = frozenset(
    {PipelineState.REJECTED, PipelineState.FAILED, PipelineState.CANCELLED}
)

_FORWARD: Final[dict[PipelineState, frozenset[PipelineState]]] = {
    PipelineState.IDEA: frozenset({PipelineState.RESEARCHED}),
    PipelineState.RESEARCHED: frozenset({PipelineState.POLICY_CHECKED}),
    PipelineState.POLICY_CHECKED: frozenset({PipelineState.GENERATING}),
    PipelineState.GENERATING: frozenset({PipelineState.GENERATED}),
    PipelineState.GENERATED: frozenset({PipelineState.QA}),
    PipelineState.QA: frozenset({PipelineState.PASSED, PipelineState.REJECTED}),
    PipelineState.PASSED: frozenset({PipelineState.ASSEMBLING}),
    PipelineState.ASSEMBLING: frozenset({PipelineState.PACKAGED}),
    PipelineState.PACKAGED: frozenset({PipelineState.UPLOADING}),
    PipelineState.UPLOADING: frozenset({PipelineState.PROCESSING}),
    PipelineState.PROCESSING: frozenset({PipelineState.PUBLISHED}),
    PipelineState.PUBLISHED: frozenset({PipelineState.ANALYZING}),
    PipelineState.ANALYZING: frozenset({PipelineState.LEARNING}),
    PipelineState.LEARNING: frozenset(),
}

_EXITS: Final[frozenset[PipelineState]] = frozenset(
    {PipelineState.HELD, PipelineState.FAILED, PipelineState.CANCELLED}
)

ALLOWED_TRANSITIONS: Final = MappingProxyType(
    {
        state: frozenset(targets | _EXITS | {state})
        for state, targets in _FORWARD.items()
    }
)
"""Lifecycle state -> every state it may move to (forward, self, HELD, FAILED, CANCELLED).
``HELD`` is handled by :func:`check_transition` because its exit depends on where the run
was held from."""


class IllegalTransitionError(ValueError):
    """A transition the PART XIV rules do not allow."""


def check_transition(
    current: PipelineState | None,
    target: PipelineState,
    *,
    held_from: PipelineState | None = None,
) -> None:
    """Raise :class:`IllegalTransitionError` unless ``current -> target`` is allowed.

    ``current is None`` is the creation of a run, which must enter ``IDEA``. Leaving
    ``HELD`` is allowed only back to ``held_from``, to ``HELD`` itself (a refused
    resume) or to ``REJECTED`` / ``FAILED`` / ``CANCELLED``.
    """
    if current is None:
        if target is not PipelineState.IDEA:
            raise IllegalTransitionError(f"a run starts in IDEA, not {target.value}")
        return
    if current in TERMINAL_STATES:
        raise IllegalTransitionError(f"{current.value} is terminal; no transition to {target.value}")
    if current is PipelineState.HELD:
        if held_from is None:
            raise IllegalTransitionError("a HELD run must record the state it was held from")
        allowed = {held_from, PipelineState.HELD, *TERMINAL_STATES}
        if target not in allowed:
            raise IllegalTransitionError(
                f"HELD (from {held_from.value}) may return only to {held_from.value} or end; "
                f"not {target.value}"
            )
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransitionError(f"{current.value} -> {target.value} is not a P27 transition")


class Step(StrEnum):
    """One activity per phase step. The value is the stable step name used in
    idempotency keys (``idempotency_key(workflow_id, step)``)."""

    RESEARCH = "research"
    POLICY_CHECK = "policy_check"
    GENERATE = "generate"
    QA = "qa"
    ASSEMBLE = "assemble"
    UPLOAD = "upload"
    SUBMIT_DISTRIBUTION = "submit_distribution"
    AWAIT_PROCESSING = "await_processing"
    EVALUATE_OUTCOME = "evaluate_outcome"
    DISPATCH_REWARD = "dispatch_reward"


STEP_ORIGIN_PHASE: Final = MappingProxyType(
    {
        Step.RESEARCH: "P3",
        Step.POLICY_CHECK: "P3",
        Step.GENERATE: "P4",
        Step.QA: "P5",
        Step.ASSEMBLE: "P6",
        Step.UPLOAD: "P7",
        Step.SUBMIT_DISTRIBUTION: "P8",
        Step.AWAIT_PROCESSING: "P7",
        Step.EVALUATE_OUTCOME: "P9",
        Step.DISPATCH_REWARD: "P9",
    }
)
"""The phase that owns each step: the ``origin_phase`` of a hold the step raises."""

PAID_STEP_CAPABILITIES: Final = MappingProxyType(
    {
        Step.GENERATE: "music.generate",
        Step.UPLOAD: "youtube.upload",
        Step.SUBMIT_DISTRIBUTION: "distribution.submit",
    }
)
"""Steps that spend money, and the ledger capability each reserves before it runs. The
P7/P8 names match their external-write guards (``youtube.upload``,
``distribution.submit``), so a guard reserving under the pipeline's idempotency key
replays the same ledger entry instead of charging twice."""


def step_activity_name(step: Step) -> str:
    """The Temporal activity name of a phase step: ``pipeline.step.<step>``."""
    return f"pipeline.step.{step.value}"


class PlatformActivity(StrEnum):
    """Temporal activity names of the platform operations the workflow calls."""

    RECORD_TRANSITION = "pipeline.record_transition"
    RESERVE_BUDGET = "pipeline.reserve_budget"
    COMMIT_BUDGET = "pipeline.commit_budget"
    RELEASE_BUDGET = "pipeline.release_budget"
    RAISE_HOLD = "pipeline.raise_hold"
    AUTHORIZE_RESUME = "pipeline.authorize_resume"
    RECORD_EXTERNAL_RESOLUTION = "pipeline.record_external_resolution"

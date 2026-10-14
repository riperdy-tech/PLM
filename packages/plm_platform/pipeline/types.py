"""Data exchanged between the pipeline workflow and its activities.

Plain frozen dataclasses of JSON-friendly fields (standard library only), so the
Temporal workflow sandbox can import them and the default data converter round-trips
them. Money crosses the boundary as :class:`MoneyAmount` (a decimal *string* plus an
ISO 4217 currency); it becomes :class:`~plm_contracts.money.Money` only inside the
activities. Every instant is timezone-aware and comes from ``workflow.now()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "BudgetDecision",
    "BudgetRequest",
    "BudgetSettlementRequest",
    "CancelSignal",
    "ExternalResolutionRequest",
    "ExternalResolutionSignal",
    "HoldInfo",
    "HoldRequest",
    "MoneyAmount",
    "OutcomeWindowClosedSignal",
    "PipelineInput",
    "PipelineOptions",
    "PipelineOutcome",
    "PipelineStatus",
    "ResumeRequest",
    "ResumeSignal",
    "ResumeVerdict",
    "SettledCost",
    "StepRequest",
    "StepResult",
    "TransitionAck",
    "TransitionRecord",
]


@dataclass(frozen=True, slots=True)
class MoneyAmount:
    """An exact amount with its currency. ``amount`` is a decimal string, never a float."""

    amount: str
    currency: str


@dataclass(frozen=True, slots=True)
class PipelineOptions:
    """Activity timeouts. Retries come from the Temporal retry policy, not from here."""

    step_start_to_close_seconds: float = 600.0
    step_heartbeat_seconds: float = 30.0
    """A step whose worker dies is retried after this long without a heartbeat."""
    step_maximum_attempts: int = 5
    platform_start_to_close_seconds: float = 30.0
    platform_maximum_attempts: int = 10


@dataclass(frozen=True, slots=True)
class PipelineInput:
    """One run per P2 directive. Everything else (the experiment, the horizon) comes
    from the steps, never from defaults."""

    directive_id: str
    brand_id: str
    options: PipelineOptions = field(default_factory=PipelineOptions)


@dataclass(frozen=True, slots=True)
class StepRequest:
    """What a phase-step activity receives."""

    workflow_id: str
    directive_id: str
    brand_id: str
    step: str
    idempotency_key: str
    requested_at: datetime
    inputs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StepResult:
    """What a phase step reports. ``cost`` is the provider-reported actual cost, ``None``
    when the provider reported none (never a zero)."""

    outputs: dict[str, Any]
    provider_ids: dict[str, str] = field(default_factory=dict)
    cost: MoneyAmount | None = None
    attempt: int = 1
    """The Temporal attempt that produced this result (stamped by the activity adapter)."""


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One append-only transition event; idempotent per (workflow_id, sequence)."""

    workflow_id: str
    sequence: int
    from_state: str | None
    to_state: str
    occurred_at: datetime
    directive_id: str
    brand_id: str
    step: str | None = None
    idempotency_key: str | None = None
    attempt: int = 0
    """Temporal attempts the step took (0: no activity ran for this transition)."""
    error_code: str | None = None
    error_detail: str | None = None
    started_at: datetime | None = None
    """When the step began (the workflow's clock), for a transition that carries a step."""
    provider_ids: dict[str, str] = field(default_factory=dict)
    cost: MoneyAmount | None = None
    cost_basis: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    hold_case_ids: tuple[str, ...] = ()
    held_from: str | None = None
    """For a transition into or within ``HELD``: the state the run returns to."""
    final: bool = False
    """The run's last transition (it completed, or ended in a terminal state)."""


@dataclass(frozen=True, slots=True)
class TransitionAck:
    sequence: int
    created: bool
    """``False`` when this (workflow_id, sequence) was already recorded identically."""


@dataclass(frozen=True, slots=True)
class BudgetRequest:
    capability: str
    idempotency_key: str
    requested_at: datetime
    estimate: MoneyAmount | None = None
    """``None``: the caller cannot price the write, so the ledger's rate card prices it."""


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reason: str
    """The ledger's reason code (``reserved``, ``budget_exhausted``, ...)."""
    reserved: MoneyAmount | None = None
    remaining: MoneyAmount | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class BudgetSettlementRequest:
    capability: str
    idempotency_key: str
    settled_at: datetime
    actual: MoneyAmount | None = None
    """For a commit: the provider-reported cost; ``None`` records the reserved estimate."""
    reason: str = ""
    """For a release: why the write did not happen."""


@dataclass(frozen=True, slots=True)
class SettledCost:
    status: str
    """``committed`` or ``released`` (ledger entry status)."""
    actual: MoneyAmount | None
    basis: str | None
    """``provider_reported`` or ``reserved_estimate`` for a commit; ``None`` for a release."""


@dataclass(frozen=True, slots=True)
class HoldRequest:
    origin_phase: str
    subject_ref: str
    reason_code: str
    raised_by: str
    raised_at: datetime
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HoldInfo:
    case_id: str
    reason_code: str
    hold_class: str
    reviewer_role: str
    required_evidence: tuple[str, ...]
    status: str
    policy_mapped: bool
    created: bool


@dataclass(frozen=True, slots=True)
class ResumeRequest:
    case_ids: tuple[str, ...]
    actor: str
    role: str | None
    evidence: dict[str, str]
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class ExternalResolutionRequest:
    case_ids: tuple[str, ...]
    actor: str
    role: str | None
    outcome: str
    """``CLEARED`` or ``ADVERSE`` (:class:`plm_platform.exceptions.ExternalOutcome`)."""
    evidence: dict[str, str]
    notes: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class ResumeVerdict:
    case_id: str
    allowed: bool
    code: str
    message: str
    case_status: str
    missing_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResumeSignal:
    """``resume(actor, role, evidence)``: ask to continue past every open hold."""

    actor: str
    role: str | None
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalResolutionSignal:
    """``external_resolution(evidence)``: the external process's outcome for a hard stop,
    recorded by an actor holding the case's reviewer role."""

    actor: str
    role: str | None
    evidence: dict[str, str]
    outcome: str = "CLEARED"
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CancelSignal:
    actor: str
    reason: str


@dataclass(frozen=True, slots=True)
class OutcomeWindowClosedSignal:
    """For a ``lifetime`` horizon, which has no end: the source closed the window."""

    actor: str
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PipelineStatus:
    """The ``status`` query: where the run is now (``None`` before the first transition)."""

    state: str | None
    sequence: int
    held_from: str | None
    open_case_ids: tuple[str, ...]
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """The workflow result."""

    workflow_id: str
    final_state: str
    completed: bool
    """``True`` only when LEARNING finished; a terminal state is ``False``."""
    sequence: int
    error_code: str | None = None

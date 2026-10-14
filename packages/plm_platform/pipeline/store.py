"""Durable pipeline state in ``ops`` (PART XIV P27; ADR-0033; migration ``0030``).

``ops.pipeline_runs``
    One row per run (Temporal workflow id): the current state, the state a ``HELD`` run
    returns to, the last recorded sequence, the last error and the completion instant.
``ops.pipeline_transitions``
    The append-only transition history (a trigger refuses UPDATE and DELETE on
    PostgreSQL). Each event records the step, its attempt and retry count, error code and
    detail, when the step started and when the transition happened, the provider ids,
    the cost (``Money``: exact amount + currency, with its basis), and the step's inputs
    and outputs (references, not artifact bytes).

Writes are idempotent per (workflow_id, sequence): a replayed event identical to the
stored one is a no-op; a different event under a recorded sequence is refused
(:class:`TransitionConflictError`). The next sequence must follow the last one and obey
:func:`~plm_platform.pipeline.states.check_transition` from the run's current state, so
the table can never record a lifecycle the workflow could not have taken. Nothing here
reads the clock: every instant is on the record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Final

import sqlalchemy as sa
from plm_contracts.money import LEDGER_QUANTUM
from sqlalchemy.orm import Session

from ..clock import require_aware
from ..db import PLATFORM_SCHEMA
from ..ledger import _LedgerAmount
from ..temporal import ValidationFailure
from .states import IllegalTransitionError, PipelineState, check_transition
from .types import MoneyAmount, TransitionAck, TransitionRecord

__all__ = [
    "PIPELINE_METADATA",
    "PipelineRun",
    "PipelineStore",
    "StoredTransition",
    "TransitionConflictError",
    "TransitionRejectedError",
    "pipeline_runs",
    "pipeline_transitions",
    "record_digest",
]

PIPELINE_METADATA: Final = sa.MetaData(schema=PLATFORM_SCHEMA)

pipeline_runs = sa.Table(
    "pipeline_runs", PIPELINE_METADATA,
    sa.Column("workflow_id", sa.String(200), primary_key=True),
    sa.Column("directive_id", sa.String(128), nullable=False),
    sa.Column("brand_id", sa.String(128), nullable=False),
    sa.Column("state", sa.String(32), nullable=False),
    sa.Column("held_from", sa.String(32), nullable=True),
    sa.Column("last_sequence", sa.Integer(), nullable=False),
    sa.Column("last_error_code", sa.String(128), nullable=True),
    sa.Column("last_error_detail", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
)
sa.Index("ix_pipeline_runs_state", pipeline_runs.c.state)
sa.Index("ix_pipeline_runs_directive", pipeline_runs.c.directive_id)

pipeline_transitions = sa.Table(
    "pipeline_transitions", PIPELINE_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column(
        "workflow_id", sa.String(200),
        sa.ForeignKey(f"{PLATFORM_SCHEMA}.pipeline_runs.workflow_id"), nullable=False,
    ),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("from_state", sa.String(32), nullable=True),
    sa.Column("to_state", sa.String(32), nullable=False),
    sa.Column("held_from", sa.String(32), nullable=True),
    sa.Column("step", sa.String(64), nullable=True),
    sa.Column("idempotency_key", sa.String(256), nullable=True),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("retry_count", sa.Integer(), nullable=False),
    sa.Column("error_code", sa.String(128), nullable=True),
    sa.Column("error_detail", sa.Text(), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("provider_ids_json", sa.JSON(), nullable=False),
    sa.Column("cost_amount", _LedgerAmount(), nullable=True),
    sa.Column("cost_currency", sa.String(3), nullable=True),
    sa.Column("cost_basis", sa.String(32), nullable=True),
    sa.Column("inputs_json", sa.JSON(), nullable=False),
    sa.Column("outputs_json", sa.JSON(), nullable=False),
    sa.Column("hold_case_ids_json", sa.JSON(), nullable=False),
    sa.Column("final", sa.Boolean(), nullable=False),
    sa.Column("record_sha256", sa.String(64), nullable=False),
    sa.UniqueConstraint("workflow_id", "sequence", name="uq_pipeline_transitions_run_seq"),
    sa.CheckConstraint(
        "(cost_amount IS NULL) = (cost_currency IS NULL)",
        name="ck_pipeline_transitions_cost_has_currency",
    ),
)
sa.Index("ix_pipeline_transitions_workflow", pipeline_transitions.c.workflow_id)


class TransitionConflictError(ValidationFailure):
    """A different event was offered under an already recorded (workflow_id, sequence)."""


class TransitionRejectedError(ValidationFailure):
    """The event breaks the lifecycle: a sequence gap, an illegal transition, or a malformed
    field (a cost without a currency, a non-decimal amount)."""


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else require_aware(moment).isoformat()


def record_digest(record: TransitionRecord) -> str:
    """SHA-256 of the canonical JSON of the whole event (instants normalised to UTC)."""
    body = asdict(record)
    body["occurred_at"] = _iso(record.occurred_at)
    body["started_at"] = _iso(record.started_at)
    body["hold_case_ids"] = list(record.hold_case_ids)
    if record.cost is not None:
        exact = _decimal(record.cost).quantize(LEDGER_QUANTUM, rounding=ROUND_HALF_EVEN)
        body["cost"] = {"amount": str(exact), "currency": record.cost.currency}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                           allow_nan=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decimal(cost: MoneyAmount) -> Decimal:
    try:
        value = Decimal(cost.amount)
    except InvalidOperation as exc:
        raise TransitionRejectedError(f"cost amount {cost.amount!r} is not a decimal") from exc
    if not value.is_finite():
        raise TransitionRejectedError(f"cost amount {cost.amount!r} is not finite")
    return value


def _transition_id(workflow_id: str, sequence: int) -> str:
    return "ptr_" + hashlib.sha256(f"{workflow_id}|{sequence}".encode()).hexdigest()[:32]


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every stored instant is UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PipelineRun:
    """A read view of one ``ops.pipeline_runs`` row."""

    workflow_id: str
    directive_id: str
    brand_id: str
    state: PipelineState
    held_from: PipelineState | None
    last_sequence: int
    last_error_code: str | None
    last_error_detail: str | None
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_row(cls, row: Any) -> PipelineRun:
        return cls(
            workflow_id=row["workflow_id"], directive_id=row["directive_id"],
            brand_id=row["brand_id"], state=PipelineState(row["state"]),
            held_from=None if row["held_from"] is None else PipelineState(row["held_from"]),
            last_sequence=int(row["last_sequence"]), last_error_code=row["last_error_code"],
            last_error_detail=row["last_error_detail"], created_at=_aware(row["created_at"]),
            updated_at=_aware(row["updated_at"]), completed_at=_aware(row["completed_at"]),
        )


@dataclass(frozen=True, slots=True)
class StoredTransition:
    """A read view of one ``ops.pipeline_transitions`` row."""

    workflow_id: str
    sequence: int
    from_state: PipelineState | None
    to_state: PipelineState
    held_from: PipelineState | None
    step: str | None
    idempotency_key: str | None
    attempt: int
    retry_count: int
    error_code: str | None
    error_detail: str | None
    started_at: datetime | None
    occurred_at: datetime | None
    provider_ids: dict[str, str]
    cost: MoneyAmount | None
    cost_basis: str | None
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    hold_case_ids: tuple[str, ...]
    final: bool
    record_sha256: str

    @classmethod
    def from_row(cls, row: Any) -> StoredTransition:
        return cls(
            workflow_id=row["workflow_id"], sequence=int(row["sequence"]),
            from_state=None if row["from_state"] is None else PipelineState(row["from_state"]),
            to_state=PipelineState(row["to_state"]),
            held_from=None if row["held_from"] is None else PipelineState(row["held_from"]),
            step=row["step"], idempotency_key=row["idempotency_key"],
            attempt=int(row["attempt"]), retry_count=int(row["retry_count"]),
            error_code=row["error_code"], error_detail=row["error_detail"],
            started_at=_aware(row["started_at"]), occurred_at=_aware(row["occurred_at"]),
            provider_ids=dict(row["provider_ids_json"] or {}),
            cost=(None if row["cost_amount"] is None
                  else MoneyAmount(amount=str(row["cost_amount"]),
                                   currency=row["cost_currency"])),
            cost_basis=row["cost_basis"], inputs=dict(row["inputs_json"] or {}),
            outputs=dict(row["outputs_json"] or {}),
            hold_case_ids=tuple(row["hold_case_ids_json"] or ()), final=bool(row["final"]),
            record_sha256=row["record_sha256"],
        )


class PipelineStore:
    """Record and read pipeline runs. The caller owns the transaction (commit/rollback)."""

    def record(self, session: Session, record: TransitionRecord) -> TransitionAck:
        """Append one transition and move the run, or confirm an identical replay."""
        if record.sequence < 1:
            raise TransitionRejectedError("sequences start at 1")
        target = self._state(record.to_state, "to_state")
        origin = None if record.from_state is None else self._state(record.from_state, "from_state")
        held_from = None if record.held_from is None else self._state(record.held_from, "held_from")
        if record.attempt < 0:
            raise TransitionRejectedError("attempt cannot be negative")
        if record.cost is not None:
            _decimal(record.cost)
            if len(record.cost.currency) != 3 or not record.cost.currency.isalpha():
                raise TransitionRejectedError(
                    f"cost currency {record.cost.currency!r} is not an ISO 4217 code"
                )
        digest = record_digest(record)

        existing = session.execute(
            sa.select(pipeline_transitions.c.record_sha256).where(
                pipeline_transitions.c.workflow_id == record.workflow_id,
                pipeline_transitions.c.sequence == record.sequence,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing != digest:
                raise TransitionConflictError(
                    f"{record.workflow_id} sequence {record.sequence} is already recorded with "
                    "different content; one sequence names one transition"
                )
            return TransitionAck(sequence=record.sequence, created=False)

        run = self.run(session, record.workflow_id, for_update=True)
        if run is None:
            if record.sequence != 1:
                raise TransitionRejectedError(
                    f"{record.workflow_id} has no run; its first transition must be sequence 1"
                )
            self._check(None, target, None, origin)
        else:
            if record.sequence != run.last_sequence + 1:
                raise TransitionRejectedError(
                    f"{record.workflow_id}: sequence {record.sequence} does not follow "
                    f"{run.last_sequence}"
                )
            if origin is not run.state:
                raise TransitionRejectedError(
                    f"{record.workflow_id} is {run.state.value}; the event says it left "
                    f"{None if origin is None else origin.value}"
                )
            self._check(run.state, target, run.held_from, origin)
        if target is PipelineState.HELD:
            expected = run.held_from if run is not None and run.state is PipelineState.HELD \
                else (None if run is None else run.state)
            if held_from is None or held_from is not expected:
                raise TransitionRejectedError(
                    "a transition into or within HELD must name the state the run returns "
                    f"to ({None if expected is None else expected.value}), got "
                    f"{None if held_from is None else held_from.value}"
                )

        at = require_aware(record.occurred_at)
        cost_amount = None if record.cost is None else _decimal(record.cost)
        transition_values: dict[str, Any] = {
            "id": _transition_id(record.workflow_id, record.sequence),
            "workflow_id": record.workflow_id, "sequence": record.sequence,
            "from_state": None if origin is None else origin.value, "to_state": target.value,
            "held_from": None if held_from is None else held_from.value,
            "step": record.step, "idempotency_key": record.idempotency_key,
            "attempt": record.attempt, "retry_count": max(record.attempt - 1, 0),
            "error_code": record.error_code, "error_detail": record.error_detail,
            "started_at": None if record.started_at is None else require_aware(record.started_at),
            "occurred_at": at, "provider_ids_json": dict(record.provider_ids),
            "cost_amount": cost_amount,
            "cost_currency": None if record.cost is None else record.cost.currency,
            "cost_basis": record.cost_basis, "inputs_json": dict(record.inputs),
            "outputs_json": dict(record.outputs),
            "hold_case_ids_json": list(record.hold_case_ids),
            "final": record.final, "record_sha256": digest,
        }
        if run is None:
            # The run row first: the transition references it.
            session.execute(pipeline_runs.insert().values(
                workflow_id=record.workflow_id, directive_id=record.directive_id,
                brand_id=record.brand_id, state=target.value, held_from=None,
                last_sequence=record.sequence, last_error_code=record.error_code,
                last_error_detail=record.error_detail, created_at=at, updated_at=at,
                completed_at=at if record.final else None,
            ))
            session.execute(pipeline_transitions.insert().values(**transition_values))
            return TransitionAck(sequence=record.sequence, created=True)

        session.execute(pipeline_transitions.insert().values(**transition_values))
        if target is PipelineState.HELD:
            next_held_from: PipelineState | None = held_from
        else:
            next_held_from = None
        values: dict[str, Any] = {
            "state": target.value, "held_from": None if next_held_from is None
            else next_held_from.value,
            "last_sequence": record.sequence, "updated_at": at,
        }
        if record.error_code is not None:
            values["last_error_code"] = record.error_code
            values["last_error_detail"] = record.error_detail
        if record.final:
            values["completed_at"] = at
        session.execute(
            pipeline_runs.update().where(pipeline_runs.c.workflow_id == record.workflow_id)
            .values(**values)
        )
        return TransitionAck(sequence=record.sequence, created=True)

    @staticmethod
    def _state(value: str, name: str) -> PipelineState:
        try:
            return PipelineState(value)
        except ValueError:
            raise TransitionRejectedError(f"{name} {value!r} is not a pipeline state") from None

    @staticmethod
    def _check(current: PipelineState | None, target: PipelineState,
               held_from: PipelineState | None, origin: PipelineState | None) -> None:
        if origin is not current:
            raise TransitionRejectedError(
                f"the event leaves {None if origin is None else origin.value}; "
                f"the run is {None if current is None else current.value}"
            )
        try:
            check_transition(current, target, held_from=held_from)
        except IllegalTransitionError as exc:
            raise TransitionRejectedError(str(exc)) from exc

    # -- reads ---------------------------------------------------------------------------

    def run(self, session: Session, workflow_id: str, *, for_update: bool = False) -> PipelineRun | None:
        query = sa.select(pipeline_runs).where(pipeline_runs.c.workflow_id == workflow_id)
        if for_update:
            query = query.with_for_update()
        row = session.execute(query).mappings().one_or_none()
        return None if row is None else PipelineRun.from_row(row)

    def transitions(self, session: Session, workflow_id: str) -> list[StoredTransition]:
        rows = session.execute(
            sa.select(pipeline_transitions)
            .where(pipeline_transitions.c.workflow_id == workflow_id)
            .order_by(pipeline_transitions.c.sequence)
        ).mappings()
        return [StoredTransition.from_row(row) for row in rows]

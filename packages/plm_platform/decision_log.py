"""The decision log (architecture PART XXI): what was decided, on what evidence, and what
happened afterwards.

Each entry records the date, the decision, its input data, the model, the confidence and
the action. The result is attached later, when the outcome is measured, so the quality of
the decision can be judged against what was known when it was made (ADR-0033: the log
lives in the platform-owned ``ops`` schema).

* ``record_decision`` is idempotent on ``decision_key``: replaying a cycle never writes a
  second entry, and a replay that differs from the stored entry is refused.
* ``record_result`` attaches a realized outcome per measurement window. The result carries
  its measurement facts (maturity, reward version, money with currency); a second, different
  result for the same window is refused.

Nothing here reads the clock: callers pass the instants.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .clock import require_aware
from .db import PLATFORM_SCHEMA

__all__ = [
    "DECISION_LOG_METADATA",
    "DecisionConflictError",
    "DecisionNotFoundError",
    "DecisionRecord",
    "allocation_decision_key",
    "decision_log",
    "load_decision",
    "record_decision",
    "record_result",
]

DECISION_LOG_METADATA: Final = sa.MetaData(schema=PLATFORM_SCHEMA)

decision_log = sa.Table(
    "decision_log", DECISION_LOG_METADATA,
    sa.Column("decision_key", sa.String(128), primary_key=True),
    sa.Column("decision_kind", sa.String(64), nullable=False),
    sa.Column("origin_phase", sa.String(32), nullable=False),
    sa.Column("subject_ref", sa.String(128), nullable=False),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("decision", sa.Text, nullable=False),
    sa.Column("action_json", sa.JSON, nullable=False),
    sa.Column("input_data_json", sa.JSON, nullable=False),
    sa.Column("model_version", sa.String(64), nullable=False),
    sa.Column("policy_version", sa.String(64), nullable=False),
    sa.Column("confidence", sa.Numeric(10, 6), nullable=True),
    sa.Column("result_json", sa.JSON, nullable=True),
    sa.Column("result_recorded_at", sa.DateTime(timezone=True), nullable=True),
)
sa.Index("ix_decision_log_subject", decision_log.c.subject_ref)


def allocation_decision_key(directive_id: str) -> str:
    """The decision behind one P2 directive; P9 reaches it through the lineage's
    ``production_directive_id``."""
    return f"allocation:{directive_id}"


class DecisionConflictError(RuntimeError):
    """A replayed decision or result differs from the one already stored."""


class DecisionNotFoundError(LookupError):
    """A result was offered for a decision that was never recorded."""


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_key: str
    decision_kind: str
    origin_phase: str
    subject_ref: str
    decided_at: datetime
    decision: str
    action: Mapping[str, Any]
    input_data: Mapping[str, Any]
    model_version: str
    policy_version: str
    confidence: Decimal | None
    """``None`` when the deciding model states no confidence; never invented."""
    result: Mapping[str, Any] | None = None
    result_recorded_at: datetime | None = None


def _row_values(record: DecisionRecord) -> dict[str, Any]:
    return {
        "decision_key": record.decision_key, "decision_kind": record.decision_kind,
        "origin_phase": record.origin_phase, "subject_ref": record.subject_ref,
        "decided_at": require_aware(record.decided_at), "decision": record.decision,
        "action_json": dict(record.action), "input_data_json": dict(record.input_data),
        "model_version": record.model_version, "policy_version": record.policy_version,
        "confidence": record.confidence,
    }


def _from_row(row: Any) -> DecisionRecord:
    def aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    confidence = row["confidence"]
    return DecisionRecord(
        decision_key=row["decision_key"], decision_kind=row["decision_kind"],
        origin_phase=row["origin_phase"], subject_ref=row["subject_ref"],
        decided_at=aware(row["decided_at"]),  # type: ignore[arg-type]
        decision=row["decision"], action=row["action_json"],
        input_data=row["input_data_json"], model_version=row["model_version"],
        policy_version=row["policy_version"],
        confidence=None if confidence is None else Decimal(str(confidence)),
        result=row["result_json"], result_recorded_at=aware(row["result_recorded_at"]),
    )



def load_decision(session: Session, decision_key: str) -> DecisionRecord | None:
    row = session.execute(
        sa.select(decision_log).where(decision_log.c.decision_key == decision_key)
    ).mappings().one_or_none()
    return None if row is None else _from_row(row)


def record_decision(session: Session, record: DecisionRecord) -> DecisionRecord:
    """Insert once; an identical replay returns the stored entry, a different one raises."""
    if record.result is not None or record.result_recorded_at is not None:
        raise ValueError("a decision is recorded without its result; attach it later")
    existing = load_decision(session, record.decision_key)
    if existing is not None:
        if _comparable(existing) != _comparable(record):
            raise DecisionConflictError(
                f"decision {record.decision_key!r} already recorded with different content"
            )
        return existing
    session.execute(sa.insert(decision_log).values(**_row_values(record)))
    return record


def _comparable(record: DecisionRecord) -> tuple[Any, ...]:
    return (
        record.decision_kind, record.origin_phase, record.subject_ref,
        require_aware(record.decided_at), record.decision, dict(record.action),
        dict(record.input_data), record.model_version, record.policy_version,
        None if record.confidence is None else Decimal(record.confidence),
    )


def record_result(session: Session, decision_key: str, *, window: str,
                  result: Mapping[str, Any], recorded_at: datetime) -> DecisionRecord:
    """Attach the realized outcome measured over ``window`` to a decision, once per window.

    The measuring phase (P9) knows the lineage back to the decision but not which window
    the allocator learns from, so every matured window is kept; the allocator's declared
    horizon (ADR-0031) picks the one that counts when the decision is assessed.
    """
    existing = load_decision(session, decision_key)
    if existing is None:
        raise DecisionNotFoundError(f"no decision {decision_key!r} to attach a result to")
    results = dict(existing.result or {})
    if window in results:
        if dict(results[window]) != dict(result):
            raise DecisionConflictError(
                f"decision {decision_key!r} already has a different {window} result"
            )
        return existing
    results[window] = dict(result)
    session.execute(
        sa.update(decision_log)
        .where(decision_log.c.decision_key == decision_key)
        .values(result_json=results, result_recorded_at=require_aware(recorded_at))
    )
    updated = load_decision(session, decision_key)
    assert updated is not None
    return updated

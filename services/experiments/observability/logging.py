"""Structured audit event logging for experiments (§80)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..storage import repositories
from ..storage.ids import deterministic_id

logger = logging.getLogger("plm.experiments")

__all__ = ["StructuredEvent", "emit_event"]


@dataclass(frozen=True, slots=True)
class StructuredEvent:
    experiment_id: str
    event: str
    state_before: str | None = None
    state_after: str | None = None
    package_id: str | None = None
    error_code: str | None = None
    worker_id: str = "worker-default"
    software_version: str = "p3-v1"
    details: dict[str, Any] | None = None


def emit_event(
    session: Session | None,
    event: StructuredEvent,
    *,
    now: datetime | None = None,
) -> str:
    """Log structured event and persist audit row if session is provided."""
    ts = now or datetime.now(UTC)
    event_id = deterministic_id("evt", event.experiment_id, event.event, str(ts.timestamp()))
    payload = asdict(event)
    payload["timestamp"] = ts.isoformat()

    logger.info("experiment_event: %s", payload)

    if session is not None:
        repositories.record_event(
            session,
            event_id=event_id,
            experiment_id=event.experiment_id,
            event_type=event.event,
            payload=payload,
            created_at=ts,
        )

    return event_id

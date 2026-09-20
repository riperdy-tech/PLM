"""Domain event base — P3 §63.

P3's ``experiment_events`` are an append-only stream; events must be versioned and
idempotent. This envelope adds ``event_version`` (the schema version of the event type;
serialised as a string, matching the P4 §68 / P5 §7 handoff examples) and
``idempotency_key`` (the name used across the specs, e.g. P7's dedupe table) to the seven
P3 fields. ``experiment_id`` is optional because not every event belongs to an experiment.

Flat handoff payloads such as P4 §68's ``music_candidate_ready_for_p5`` travel inside
``payload`` when carried on this envelope; unknown top-level fields are refused so the
seam cannot silently drift.
"""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

__all__ = ["EventEnvelope"]


class EventEnvelope(BaseModel):
    """P3 §63 event envelope, frozen for append-only use."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    experiment_id: str | None = None
    event_type: str = Field(min_length=1)
    occurred_at: AwareDatetime
    actor: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    software_version: str = Field(min_length=1)
    event_version: str = Field(default="1", min_length=1)
    idempotency_key: str | None = None

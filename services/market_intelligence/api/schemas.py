# services/market_intelligence/api/schemas.py
"""The frozen C1 wire shapes (registry `docs/contracts/CONTRACT_REGISTRY.md`, entry C1).

Field names here are not ours to rename: P2's adapter reads `market_opportunity_score`
into `p1_score` and `confidence` into `p1_confidence` (registry C1). `extra="forbid"`
on every model so a drift is a test failure, not a silently dropped field.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Annotated, Any

from plm_contracts.seams.c1 import (
    ComponentsPayload,
    EvidencePayload,
    OpportunityPagePayload,
    OpportunityPayload,
    TaxonomyPayload,
)
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ArmOut", "ComponentsOut", "EvidenceOut", "ObservationOut", "OpportunityDetail",
    "OpportunityListResponse", "OpportunityOut", "RunOut", "TaxonomyOut", "decode_cursor",
    "encode_cursor",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


TaxonomyOut = TaxonomyPayload
ComponentsOut = ComponentsPayload
EvidenceOut = EvidencePayload
OpportunityOut = OpportunityPayload
"""Registry C1 item; defined once in ``plm_contracts.seams.c1`` (ADR-0025)."""
OpportunityListResponse = OpportunityPagePayload
"""The versioned C1 envelope served by ``GET /market/v1/opportunities``."""


class ArmOut(_Frozen):
    arm_id: str
    fingerprint: str
    status: str
    genre: str | None = None
    subgenre: str | None = None
    mood: list[str] = Field(default_factory=list)
    use_case: str | None = None
    language: str | None = None
    vocal_mode: str | None = None
    vocal_gender: str | None = None
    energy: str | None = None
    bpm_range: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    audience_intent: str | None = None
    title_archetype: str | None = None
    support_count: int = 0


class OpportunityDetail(_Frozen):
    """§25's detail shape; the 13-dimension arm object lives here (R14 dimensions)."""

    arm: ArmOut
    score: Annotated[float, Field(ge=0.0, le=100.0)]
    component_scores: ComponentsOut
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence_summary: EvidenceOut
    observation_counts: Annotated[int, Field(ge=0)]
    source_counts: Annotated[int, Field(ge=0)]
    last_updated: datetime


class ObservationOut(_Frozen):
    id: str
    entity_id: str
    source_id: str
    observed_at: datetime
    published_at: datetime | None = None
    metrics: dict[str, Any]
    text_title: str | None = None
    text_description: str | None = None
    provenance: dict[str, Any]


class RunOut(_Frozen):
    id: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    counts: dict[str, int]
    costs: dict[str, float]
    error_summary: dict[str, Any] | None = None
    software_version: str


def encode_cursor(score: float, arm_id: str) -> str:
    """Opaque keyset cursor over `(score desc, arm_id asc)` — stable under insertion."""
    payload = json.dumps({"s": score, "a": arm_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_cursor(cursor: str) -> tuple[float, str]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return float(decoded["s"]), str(decoded["a"])
    except Exception as exc:  # any malformed cursor is one message
        raise ValueError(f"invalid cursor {cursor!r}") from exc

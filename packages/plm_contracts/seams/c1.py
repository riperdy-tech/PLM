"""Seam C1 · PHASE-P1 → PHASE-P2: the market opportunity page (R14, R15, ADR-0022, ADR-0025).

P1 serves ``GET /market/v1/opportunities`` as :class:`OpportunityPagePayload`, a flat,
versioned envelope (ADR-0022), and P2 parses it with the same model. Unknowns are
explicit:

* a score component the evidence does not support is ``null``, never ``0`` (§3.6);
* a taxonomy dimension that is not part of the arm is ``null`` or an empty list. The arm
  is open on that dimension; the value is not missing.

Evidence counts are always present. ``freshness`` is deliberately absent: P2 derives it
from ``as_of``.
"""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .base import SeamModel

__all__ = [
    "ComponentsPayload",
    "EvidencePayload",
    "OpportunityPagePayload",
    "OpportunityPayload",
    "TaxonomyPayload",
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaxonomyPayload(_Closed):
    """The nine C1 taxonomy keys; ``None`` / ``[]`` = the arm is open on that dimension."""

    genre: str | None
    subgenre: str | None
    mood: list[str]
    use_case: list[str]
    language: str | None
    vocal_mode: str | None
    duration_bucket: str | None
    visual_world: str | None
    title_archetype: str | None


class ComponentsPayload(_Closed):
    """The seven score components; ``None`` = not supported by the evidence."""

    demand: float | None
    growth: float | None
    competitor_velocity: float | None
    competition: float | None
    audience_quality: float | None
    cross_platform: float | None
    production_fit: float | None


class EvidencePayload(_Closed):
    observation_count: Annotated[int, Field(ge=0)]
    source_count: Annotated[int, Field(ge=0)]
    top_observation_ids: list[str]


class OpportunityPayload(_Closed):
    """One scored arm (registry C1)."""

    arm_id: str = Field(min_length=1)
    market_opportunity_score: Annotated[float, Field(ge=0.0, le=100.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    as_of: AwareDatetime
    taxonomy: TaxonomyPayload
    components: ComponentsPayload
    evidence: EvidencePayload
    score_version: str = Field(min_length=1)


class OpportunityPagePayload(SeamModel):
    """The flat, versioned C1 envelope."""

    SEAM: ClassVar[str] = "C1"
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    items: tuple[OpportunityPayload, ...]
    next_cursor: str | None
    """``None`` = no further page."""
    as_of: AwareDatetime
    score_version: str = Field(min_length=1)

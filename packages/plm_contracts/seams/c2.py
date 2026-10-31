"""Seam C2 · PHASE-P2 → PHASE-P3: the production directive (R4, ADR-0023, ADR-0025).

P2's directive is the wire format. P3 parses it with :class:`ProductionDirectivePayload`
and maps names on its own side (``quantity`` → ``requested_experiment_count``,
``allocation_type`` → ``exploration_level``, ``expires_at`` → ``production_deadline``).
Every field is required: P3 may not invent ``expected_value``, versions or context.

Schema 2.0 (ADR-0031) adds ``outcome_horizon``: the one observation window whose mature
outcome P2 will learn from. P3 records it in the experiment's measurement plan at design
time, so the horizon is declared before any result exists.

Schema 2.1 (ADR-0044 amendment, 2026-11-01) adds the optional ``brief.subgenre``: the
arm's sub-style, which selects P3's sub-style template. A minor version only adds an
optional field (ADR-0025), so a 2.0 payload without it still parses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..enums import AllocationType, ObservationWindow
from .base import SeamModel

__all__ = ["C2_WIRE_FIELDS", "DirectiveBrief", "ProductionDirectivePayload"]


class DirectiveBrief(BaseModel):
    """P2 §33: a creative search region, not an over-constrained song prompt.

    Every dimension is optional by design: an unset dimension is open for exploration,
    not missing data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    genre: str | None = None
    subgenre: str | None = None
    mood: tuple[str, ...] = ()
    use_case: tuple[str, ...] = ()
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None


class ProductionDirectivePayload(SeamModel):
    """The seventeen frozen C2 fields, ``outcome_horizon`` (2.0) and ``schema_version``.

    2.1 adds only the optional ``brief.subgenre``."""

    SEAM: ClassVar[str] = "C2"
    SCHEMA_VERSION: ClassVar[str] = "2.1"

    directive_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    arm_id: str = Field(min_length=1)
    allocation_type: AllocationType
    priority: Annotated[int, Field(ge=1)]
    quantity: Annotated[int, Field(ge=1)]
    brief: DirectiveBrief
    # No upper bound: the scale belongs to the versioned value model, not the wire.
    expected_value: float = Field(ge=0.0)
    uncertainty: float = Field(ge=0.0)
    budget_limit: float = Field(ge=0.0)
    market_context: Mapping[str, Any]
    selection_reason: tuple[str, ...]
    directive_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    expires_at: AwareDatetime
    exploration_fraction: float = Field(ge=0.0, le=1.0)
    value_model: str = Field(min_length=1)
    outcome_horizon: ObservationWindow


C2_WIRE_FIELDS: tuple[str, ...] = tuple(ProductionDirectivePayload.model_fields)
"""``schema_version``, the seventeen registry fields in registry order, then
``outcome_horizon`` (schema 2.0, ADR-0031)."""

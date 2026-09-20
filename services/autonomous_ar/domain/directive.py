"""The production directive (§32, §120) — P2's side of frozen seam C2 (ADR-0023).

The seventeen wire fields are listed in the registry and pinned by P2.1A. Everything
in §52 beyond them (`id`, `status`, `brief_json`, `created_at`, `consumed_at`) is
persistence-only and must never appear on the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from plm_contracts import AllocationType
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DirectiveBrief", "ProductionDirective"]


class DirectiveBrief(BaseModel):
    """§33: a creative search region, not an over-constrained song prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    genre: str | None = None
    mood: tuple[str, ...] = ()
    use_case: tuple[str, ...] = ()
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None


class ProductionDirective(BaseModel):
    """The exact registry-C2 shape. `to_wire()` and the field constant land in P2.1A."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directive_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    arm_id: str = Field(min_length=1)
    allocation_type: AllocationType
    priority: Annotated[int, Field(ge=1)]
    quantity: Annotated[int, Field(ge=1)]
    brief: DirectiveBrief
    expected_value: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0)
    budget_limit: float = Field(ge=0.0)
    market_context: Mapping[str, Any]
    selection_reason: tuple[str, ...]
    directive_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    expires_at: datetime
    exploration_fraction: float = Field(ge=0.0, le=1.0)
    value_model: str = Field(min_length=1)

"""The production directive (§32, §120) — P2's side of frozen seam C2 (ADR-0023).

The seventeen wire fields are listed in the registry and pinned by P2.1A. Everything
in §52 beyond them (`id`, `status`, `brief_json`, `created_at`, `consumed_at`) is
persistence-only and must never appear on the wire.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from plm_contracts import AllocationType
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DIRECTIVE_WIRE_FIELDS", "DirectiveBrief", "DirectiveWireError", "ProductionDirective",
]


class DirectiveWireError(RuntimeError):
    """Raised when the serialised directive drifts from the frozen C2 wire (ADR-0023)."""


DIRECTIVE_WIRE_FIELDS: tuple[str, ...] = (
    "directive_id", "decision_id", "arm_id", "allocation_type", "priority", "quantity",
    "brief", "expected_value", "uncertainty", "budget_limit", "market_context",
    "selection_reason", "directive_version", "policy_version", "expires_at",
    "exploration_fraction", "value_model",
)
"""The frozen C2 producer fields, in registry order (ADR-0023). Persistence-only fields
(`id`, `status`, `brief_json`, `created_at`, `consumed_at`, and the extra columns P2.3
stores to serve the wire) must never be added here — a wire change is a registry
amendment first (R2)."""


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
    """The exact registry-C2 shape (seventeen frozen fields); `to_wire()` serialises it
    and is what the C2 seam test pins."""

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

    def to_wire(self) -> dict[str, Any]:
        """The JSON-safe payload that crosses C2; keys are exactly DIRECTIVE_WIRE_FIELDS."""
        payload: dict[str, Any] = json.loads(self.model_dump_json())
        if tuple(payload) != DIRECTIVE_WIRE_FIELDS:
            raise DirectiveWireError(
                f"C2 wire drift: produced {tuple(payload)}; "
                f"registry freezes {DIRECTIVE_WIRE_FIELDS}"
            )
        return payload

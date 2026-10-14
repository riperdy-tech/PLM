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
from plm_contracts.seams.c2 import C2_WIRE_FIELDS, DirectiveBrief, ProductionDirectivePayload
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DIRECTIVE_WIRE_FIELDS", "DirectiveBrief", "DirectiveWireError", "ProductionDirective",
]


class DirectiveWireError(RuntimeError):
    """Raised when the serialised directive drifts from the frozen C2 wire (ADR-0023)."""


DIRECTIVE_WIRE_FIELDS: tuple[str, ...] = C2_WIRE_FIELDS
"""``schema_version`` plus the frozen C2 fields, in registry order (ADR-0023, ADR-0025).
Defined once in ``plm_contracts.seams.c2``. Persistence-only fields
(`id`, `status`, `brief_json`, `created_at`, `consumed_at`, and the extra columns P2.3
stores to serve the wire) must never be added here — a wire change is a registry
amendment first (R2)."""


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
    # Deliberately no upper bound. The range belongs to the value model's normalisation, not to
    # the wire: while `value_model` is the normalised early-stage proxy it happens to be 0-1, but
    # the moment P9 supplies the full economic terms (revenue, subscriber value, catalog value,
    # ...) the scale changes. Pinning `le=1.0` here would convert that expected change into a wire
    # change against a frozen seam, so the bound is left to the model (controller review finding).
    expected_value: float = Field(ge=0.0)
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
        """The JSON-safe payload that crosses C2; keys are exactly DIRECTIVE_WIRE_FIELDS.

        Serialised through the shared seam model, so P2 and P3 read one definition.
        """
        payload = ProductionDirectivePayload.build(
            **json.loads(self.model_dump_json())
        ).to_wire()
        if tuple(payload) != DIRECTIVE_WIRE_FIELDS:
            raise DirectiveWireError(
                f"C2 wire drift: produced {tuple(payload)}; "
                f"registry freezes {DIRECTIVE_WIRE_FIELDS}"
            )
        return payload

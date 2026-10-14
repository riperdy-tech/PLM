"""Ingestion and adaptation of P2 production directives (Seam C2, PHASE-P3 §10, §11).

The wire is parsed with the shared :class:`~plm_contracts.seams.c2.ProductionDirectivePayload`
(ADR-0025): every field is required, unknown fields are refused, and nothing is invented
on this side. P3 only renames (R4, ADR-0023):

* ``requested_experiment_count`` <- ``quantity``
* ``exploration_level`` <- ``allocation_type``
* ``production_deadline`` <- ``expires_at``
* ``required_constraints`` / ``recommended_attributes`` <- the brief, split into hard
  constraints and soft preferences
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from plm_contracts import AllocationType, ErrorShape, ObservationWindow
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c2 import ProductionDirectivePayload
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AdaptedDirective", "DirectiveValidationError", "adapt_directive"]

HARD_CONSTRAINT_KEYS: frozenset[str] = frozenset({"genre", "language", "vocal_mode"})
"""Brief dimensions P3 treats as hard constraints; the rest are recommendations."""


class DirectiveValidationError(ValueError):
    """An incoming P2 directive was refused; ``error`` carries the reason code and fields."""

    def __init__(self, error: ErrorShape) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")


class AdaptedDirective(BaseModel):
    """P3's internal view of one P2 directive. Every field comes from the wire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directive_id: str
    decision_id: str
    arm_id: str
    exploration_level: AllocationType
    priority: int = Field(ge=1)
    requested_experiment_count: int = Field(ge=1)
    production_deadline: datetime
    budget_limit: float = Field(ge=0.0)  # directive share ceiling (ADR-0023 #1)
    brief: dict[str, Any]
    required_constraints: dict[str, Any]
    recommended_attributes: dict[str, Any]
    market_context: dict[str, Any]
    selection_reason: list[str]
    expected_value: float = Field(ge=0.0)
    uncertainty: float = Field(ge=0.0)
    value_model: str
    directive_version: str
    policy_version: str
    outcome_horizon: ObservationWindow


def adapt_directive(wire_payload: Mapping[str, Any]) -> AdaptedDirective:
    """Parse a C2 directive and map it to P3's names; refuse anything incomplete."""
    try:
        wire = parse_seam(ProductionDirectivePayload, wire_payload)
    except SeamRejection as rejection:
        raise DirectiveValidationError(rejection.error) from rejection

    brief = wire.brief.model_dump(mode="json")
    present = {k: v for k, v in brief.items() if v not in (None, [], ())}
    return AdaptedDirective(
        directive_id=wire.directive_id,
        decision_id=wire.decision_id,
        arm_id=wire.arm_id,
        exploration_level=wire.allocation_type,
        priority=wire.priority,
        requested_experiment_count=wire.quantity,
        production_deadline=wire.expires_at,
        budget_limit=wire.budget_limit,
        brief=brief,
        required_constraints={k: v for k, v in present.items() if k in HARD_CONSTRAINT_KEYS},
        recommended_attributes={
            k: v for k, v in present.items() if k not in HARD_CONSTRAINT_KEYS
        },
        market_context=dict(wire.market_context),
        selection_reason=list(wire.selection_reason),
        expected_value=wire.expected_value,
        uncertainty=wire.uncertainty,
        value_model=wire.value_model,
        directive_version=wire.directive_version,
        policy_version=wire.policy_version,
        outcome_horizon=wire.outcome_horizon,
    )

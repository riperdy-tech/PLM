"""Ingestion and adaptation of P2 Production Directives (Seam C2, PHASE-P3 §10, §11, ADR-0023)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class DirectiveValidationError(Exception):
    """Raised when an incoming P2 directive fails schema or fiduciary contract validation."""


class AdaptedDirective(BaseModel):
    """P3 internal representation of an ingested P2 directive (§10, §11, ADR-0023)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directive_id: str
    decision_id: str
    arm_id: str
    exploration_level: str  # exploit | adjacent_explore | radical_explore
    priority: int = Field(ge=1)
    requested_experiment_count: int = Field(ge=1)  # mapped from quantity
    production_deadline: datetime  # mapped from expires_at
    budget_limit: float = Field(ge=0.0)  # directive share ceiling (ADR-0023 #1)
    brief: dict[str, Any]
    required_constraints: dict[str, Any]
    recommended_attributes: dict[str, Any]
    market_context: dict[str, Any] = Field(default_factory=dict)
    selection_reason: list[str] = Field(default_factory=list)
    expected_value: float = Field(ge=0.0)
    uncertainty: float = Field(ge=0.0)
    value_model: str
    directive_version: str
    policy_version: str


def adapt_directive(wire_payload: dict[str, Any]) -> AdaptedDirective:
    """Validate and adapt a frozen C2 directive payload into P3's internal domain contract.

    Mappings per Ruling R4 and ADR-0023:
    * requested_experiment_count ← quantity
    * exploration_level ← allocation_type (exploit / adjacent_explore / radical_explore)
    * production_deadline ← expires_at
    * required_constraints ← hard constraints from brief
    * recommended_attributes ← soft preferences from brief
    """
    # Enforce non-negotiable identity fields (§10)
    for req in ("directive_id", "decision_id", "arm_id"):
        if not wire_payload.get(req):
            raise DirectiveValidationError(f"Directive is missing required identity field: {req}")

    allocation_type = str(wire_payload.get("allocation_type", ""))
    if allocation_type not in {"exploit", "adjacent_explore", "radical_explore"}:
        raise DirectiveValidationError(
            f"Invalid allocation_type '{allocation_type}'; must be one of "
            "exploit, adjacent_explore, radical_explore"
        )

    quantity = wire_payload.get("quantity")
    if not isinstance(quantity, int) or quantity < 1:
        raise DirectiveValidationError(f"quantity must be an integer >= 1; got {quantity}")

    budget_limit = wire_payload.get("budget_limit")
    if budget_limit is None or float(budget_limit) < 0.0:
        raise DirectiveValidationError(f"budget_limit must be >= 0.0; got {budget_limit}")

    brief = dict(wire_payload.get("brief", {}))

    # Extract hard constraints vs recommended attributes
    required_constraints = {
        k: v for k, v in brief.items() if k in {"genre", "language", "vocal_mode"} and v
    }
    recommended_attributes = {
        k: v for k, v in brief.items() if k not in {"genre", "language", "vocal_mode"} and v
    }

    try:
        return AdaptedDirective(
            directive_id=wire_payload["directive_id"],
            decision_id=wire_payload["decision_id"],
            arm_id=wire_payload["arm_id"],
            exploration_level=allocation_type,
            priority=int(wire_payload.get("priority", 1)),
            requested_experiment_count=quantity,
            production_deadline=wire_payload["expires_at"],
            budget_limit=float(budget_limit),
            brief=brief,
            required_constraints=required_constraints,
            recommended_attributes=recommended_attributes,
            market_context=dict(wire_payload.get("market_context", {})),
            selection_reason=list(wire_payload.get("selection_reason", [])),
            expected_value=float(wire_payload.get("expected_value", 0.0)),
            uncertainty=float(wire_payload.get("uncertainty", 0.0)),
            value_model=str(wire_payload.get("value_model", "p2-value-proxy-v1")),
            directive_version=str(wire_payload.get("directive_version", "p2-dir-v1")),
            policy_version=str(wire_payload.get("policy_version", "p2-v1")),
        )
    except (ValidationError, KeyError, TypeError, ValueError) as err:
        raise DirectiveValidationError(f"Invalid C2 directive payload: {err}") from err

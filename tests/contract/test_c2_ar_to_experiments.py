# tests/contract/test_c2_ar_to_experiments.py
"""Seam C2 — PHASE-P2 → PHASE-P3 (production directive). FROZEN by ADR-0023 (R4).

Producer: P2's `ProductionDirective.to_wire()` — the real serialiser, not a stub.
Consumer schema: the registry's frozen producer field list (the wire P3's adapter reads).

Scope note (stated so it is never mistaken for the whole seam): this is P2's producer
half. P3's consumer half — its adapter, the `exploration_level ← allocation_type` map and
the `requested_experiment_count ← quantity` rename against P3's code — lands in P3.4,
after which `tests/contract/test_c2_ar_to_experiments.py` gains those cases in the same
file. A wire change is a registry amendment first (R2), then both sides in one change.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from plm_contracts import AllocationType, to_exploration_level
from pydantic import BaseModel, ConfigDict

from services.autonomous_ar.domain.directive import (
    DIRECTIVE_WIRE_FIELDS,
    DirectiveBrief,
    ProductionDirective,
)
from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C2 · PHASE-P2 → PHASE-P3"

REGISTRY_FIELDS: tuple[str, ...] = (
    "directive_id", "decision_id", "arm_id", "allocation_type", "priority", "quantity",
    "brief", "expected_value", "uncertainty", "budget_limit", "market_context",
    "selection_reason", "directive_version", "policy_version", "expires_at",
    "exploration_fraction", "value_model",
)
REGISTRY_BRIEF_FIELDS: tuple[str, ...] = (
    "genre", "mood", "use_case", "language", "vocal_mode", "duration_bucket", "visual_world",
)


class BriefSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    genre: str | None = None
    mood: list[str] = []
    use_case: list[str] = []
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None


class ConsumerSchema(BaseModel):
    """P3's expected wire schema, field names copied from the registry entry."""

    model_config = ConfigDict(extra="forbid")

    directive_id: str
    decision_id: str
    arm_id: str
    allocation_type: str
    priority: int
    quantity: int
    brief: BriefSchema
    expected_value: float
    uncertainty: float
    budget_limit: float
    market_context: dict[str, Any]
    selection_reason: list[str]
    directive_version: str
    policy_version: str
    expires_at: datetime
    exploration_fraction: float
    value_model: str


def producer_directive() -> ProductionDirective:
    """The producer: P2's real directive serialiser. Replaced by a cycle-produced
    directive in P2.10 (same assertions, a stronger producer)."""
    return ProductionDirective(
        directive_id="directive-1", decision_id="decision-1",
        arm_id="rnb_late_night_drive_english_vocal_45_75m",
        allocation_type=AllocationType.EXPLOIT, priority=1, quantity=1,
        brief=DirectiveBrief(genre="rnb", mood=("melancholic", "dreamy"),
                             use_case=("late_night_drive",), language="english",
                             vocal_mode="vocal", duration_bucket="45_75m",
                             visual_world="rainy_asian_city"),
        expected_value=0.72, uncertainty=0.31, budget_limit=0.5,
        market_context={"p1_score": 82.0, "p1_confidence": 0.7, "freshness_seconds": 3600.0,
                        "score_version": "p1-v1", "measured_component_count": 4},
        selection_reason=("STRONG_POSTERIOR", "LOW_FATIGUE"),
        directive_version="p2-directive-v1", policy_version="p2-v1",
        expires_at=datetime(2026, 9, 27, 6, 0, tzinfo=UTC),
        exploration_fraction=0.70, value_model="early-stage-proxy-v1",
    )


def test_declared_fields_match_the_registry_field_list() -> None:
    assert DIRECTIVE_WIRE_FIELDS == REGISTRY_FIELDS
    assert set(ConsumerSchema.model_fields) == set(DIRECTIVE_WIRE_FIELDS)
    assert set(BriefSchema.model_fields) == set(REGISTRY_BRIEF_FIELDS)


def test_producer_payload_validates_against_the_consumer_schema() -> None:
    payload = json.loads(json.dumps(producer_directive().to_wire()))
    assert_seam_payload(payload, ConsumerSchema, seam=REGISTRY_ENTRY,
                        producer="ProductionDirective.to_wire",
                        consumer="P3 adapter schema (registry C2)")


def test_frozen_vocabulary_and_rename_direction() -> None:
    # ADR-0023 #2: the three discrete levels are the contract; the map is the identity.
    assert to_exploration_level(AllocationType.EXPLOIT) == "exploit"
    assert to_exploration_level(AllocationType.ADJACENT_EXPLORE) == "adjacent_explore"
    assert to_exploration_level(AllocationType.RADICAL_EXPLORE) == "radical_explore"
    # P3's internal names are the consumer's, never the producer's (R4): if either rename
    # ever appears on P2's wire, the seam has been broken from the wrong side.
    for consumer_only in ("requested_experiment_count", "exploration_level",
                          "production_deadline", "required_constraints"):
        assert consumer_only not in DIRECTIVE_WIRE_FIELDS


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists, "the registry was frozen 2026-09-20; it cannot be missing"
    contract_registry.require_entry(REGISTRY_ENTRY)

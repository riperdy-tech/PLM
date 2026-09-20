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
    """The producer: a directive produced by the real allocation cycle (P2.10), not a
    hand-built fixture — the P2.1A note promised this swap. Every assertion below is
    unchanged; the payload now proves the engine's own output crosses the frozen wire."""
    from services.autonomous_ar.config import load_ar_settings
    from services.autonomous_ar.decisioning.cycle import (
        CycleRequest,
        MarketSnapshot,
        run_allocation_cycle,
    )
    from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
    from services.autonomous_ar.domain.policy import DEFAULT_POLICY
    from services.autonomous_ar.storage import repositories
    from tests.support.ar_db import ar_session, create_ar_engine

    now = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    arm = ContentArm(
        arm_id="rnb_late_night_drive_english_vocal_45_75m",
        dimensions=ArmDimensions(genre="rnb", moods=("melancholic", "dreamy"),
                                 use_cases=("late_night_drive",), language="english",
                                 vocal_mode="vocal", duration_bucket="45_75m",
                                 visual_world="rainy_asian_city"),
        p1_score=82.0, p1_confidence=0.7,
        component_scores={name: 60.0 for name in
                          ("demand", "growth", "competitor_velocity", "competition",
                           "audience_quality", "cross_platform", "production_fit")},
        observation_count=6, source_count=2, as_of=now, score_version="p1-v1",
        freshness_seconds=3600.0,
    )
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul",
                                   created_at=now)
        result = run_allocation_cycle(
            session,
            CycleRequest(brand_id="neon_seoul", as_of=now, slot_count=1, budget=1.0,
                         policy_version="p2-v1"),
            settings=load_ar_settings(), market=MarketSnapshot(arms=(arm,), as_of=now,
                                                               source="live",
                                                               score_version="p1-v1"),
            now=now,
        )
    return result.directives[0]


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

# tests/contract/test_c2_ar_to_experiments.py
"""Seam C2 — PHASE-P2 → PHASE-P3 (production directive). FROZEN by ADR-0023 (R4), ADR-0025.

Producer: P2's real allocation cycle, serialised by ``ProductionDirective.to_wire()``.
Consumer: P3's real ``adapt_directive``. Both read the shared
``plm_contracts.seams.c2.ProductionDirectivePayload``; this file proves the real producer's
output is accepted, mapped without invention, and that the consumer refuses every
incomplete, unknown-field or future-version payload.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts import AllocationType
from plm_contracts.seams.c2 import C2_WIRE_FIELDS

from services.autonomous_ar.domain.directive import DIRECTIVE_WIRE_FIELDS, ProductionDirective
from services.experiments.ingestion.p2_adapter import DirectiveValidationError, adapt_directive
from tests.support.contract import SeamRegistry, assert_refused, with_value, without

REGISTRY_ENTRY = "C2 · PHASE-P2 → PHASE-P3"

REGISTRY_FIELDS: tuple[str, ...] = (
    "directive_id",
    "decision_id",
    "arm_id",
    "allocation_type",
    "priority",
    "quantity",
    "brief",
    "expected_value",
    "uncertainty",
    "budget_limit",
    "market_context",
    "selection_reason",
    "directive_version",
    "policy_version",
    "expires_at",
    "exploration_fraction",
    "value_model",
    "outcome_horizon",  # schema 2.0, ADR-0031
)


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
        dimensions=ArmDimensions(
            genre="rnb",
            moods=("melancholic", "dreamy"),
            use_cases=("late_night_drive",),
            language="english",
            vocal_mode="vocal",
            duration_bucket="45_75m",
            visual_world="rainy_asian_city",
        ),
        p1_score=82.0,
        p1_confidence=0.7,
        component_scores={
            name: 60.0
            for name in (
                "demand",
                "growth",
                "competitor_velocity",
                "competition",
                "audience_quality",
                "cross_platform",
                "production_fit",
            )
        },
        observation_count=6,
        source_count=2,
        as_of=now,
        score_version="p1-v1",
        freshness_seconds=3600.0,
    )
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul", created_at=now)
        result = run_allocation_cycle(
            session,
            CycleRequest(
                brand_id="neon_seoul", as_of=now, slot_count=1, budget=1.0, policy_version="p2-v1"
            ),
            settings=load_ar_settings(),
            market=MarketSnapshot(arms=(arm,), as_of=now, source="live", score_version="p1-v1"),
            now=now,
        )
    return result.directives[0]


def _wire() -> dict[str, Any]:
    return json.loads(json.dumps(producer_directive().to_wire()))


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)


def test_wire_is_schema_version_plus_the_registry_fields() -> None:
    assert C2_WIRE_FIELDS == DIRECTIVE_WIRE_FIELDS == ("schema_version", *REGISTRY_FIELDS)
    assert tuple(_wire()) == C2_WIRE_FIELDS


def test_real_producer_payload_is_adapted_without_invention() -> None:
    directive = producer_directive()
    wire = _wire()
    adapted = adapt_directive(wire)

    assert adapted.directive_id == directive.directive_id
    assert adapted.decision_id == directive.decision_id
    assert adapted.arm_id == directive.arm_id
    assert adapted.exploration_level is AllocationType(directive.allocation_type)
    assert adapted.requested_experiment_count == directive.quantity
    assert adapted.production_deadline == directive.expires_at
    assert adapted.priority == directive.priority
    assert adapted.budget_limit == directive.budget_limit
    # The fields P3 used to default must arrive with the producer's own values.
    assert adapted.expected_value == directive.expected_value
    assert adapted.uncertainty == directive.uncertainty
    assert adapted.value_model == directive.value_model
    assert adapted.outcome_horizon == directive.outcome_horizon
    assert adapted.directive_version == directive.directive_version
    assert adapted.policy_version == directive.policy_version
    assert adapted.market_context == dict(directive.market_context)
    assert adapted.selection_reason == list(directive.selection_reason)


@pytest.mark.parametrize("field", C2_WIRE_FIELDS)
def test_consumer_refuses_a_payload_missing_any_field(field: str) -> None:
    exc = assert_refused(
        adapt_directive, without(_wire(), field), DirectiveValidationError,
        because=f"{field} missing",
    )
    assert isinstance(exc, DirectiveValidationError)
    assert exc.error.code == "missing_required_field"
    assert field in exc.error.field_paths


def test_consumer_refuses_an_unknown_field() -> None:
    exc = assert_refused(
        adapt_directive, with_value(_wire(), "surprise", 1), DirectiveValidationError,
        because="unknown field",
    )
    assert isinstance(exc, DirectiveValidationError)
    assert exc.error.code == "unknown_field"


def test_consumer_refuses_a_future_major_version() -> None:
    exc = assert_refused(
        adapt_directive, with_value(_wire(), "schema_version", "3.0"), DirectiveValidationError,
        because="future major",
    )
    assert isinstance(exc, DirectiveValidationError)
    assert exc.error.code == "unsupported_schema_version"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allocation_type", "yolo"),
        ("budget_limit", -1.0),
        ("quantity", 0),
        ("arm_id", ""),
        ("expires_at", "2026-11-01T00:00:00"),  # naive timestamp: ambiguous deadline
    ],
)
def test_consumer_refuses_invalid_values(field: str, value: Any) -> None:
    assert_refused(
        adapt_directive, with_value(_wire(), field, value), DirectiveValidationError,
        because=f"{field}={value!r}",
    )


# tests/contract/test_c8_analytics_to_p10.py
"""Seam C8 — PHASE-P9 → PHASE-P10 (deferred feature seam). FROZEN by Ruling R12.

Producer: P9's `P10FeatureView` via `build_p10_feature_view()` / `GET /analytics/p10/features`.
Consumer: PHASE-P10 (deferred per Ruling R12, preserved so interface does not rot).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from analytics.domain.enums import ObservationWindow
from analytics.domain.models import ExperimentOutcomeRecord
from analytics.projection import build_p10_feature_view
from pydantic import BaseModel, ConfigDict

from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C8 · PHASE-P9 → PHASE-P10"

REGISTRY_FIELDS: tuple[str, ...] = (
    "unit_id",
    "experiment_id",
    "arm_id",
    "content_genome_id",
    "observation_window",
    "maturity",
    "primary_outcome",
    "secondary_outcomes",
    "quality",
)


class PrimaryOutcome(BaseModel):
    model_config = ConfigDict(extra="allow")
    metric: str
    value: float
    status: str


class SecondaryOutcomes(BaseModel):
    model_config = ConfigDict(extra="allow")
    views: int
    watch_time_hours: float
    subscribers_gained: int
    spotify_streams: int


class QualityMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")
    data_completeness: float
    fidelity: float
    contamination: float
    attribution_confidence: float


class ConsumerP10Schema(BaseModel):
    """P10's expected feature view schema for C8, fields copied from registry entry C8."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    experiment_id: str
    arm_id: str
    content_genome_id: str
    observation_window: str
    maturity: str
    primary_outcome: dict[str, Any]
    secondary_outcomes: dict[str, Any]
    quality: dict[str, Any]


def test_declared_fields_match_registry() -> None:
    assert set(ConsumerP10Schema.model_fields) == set(REGISTRY_FIELDS)


def test_producer_event_validates_against_consumer_schema(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)

    outcome = ExperimentOutcomeRecord(
        id="outc_exp_synth_01_arm_synth_01_7d",
        experiment_id="exp_synth_01",
        experiment_family_id="fam_synth_01",
        content_arm_id="arm_synth_01",
        content_genome_id="gen_synth_01",
        unit_id="pkg_synth_01",
        observation_window=ObservationWindow.W_7D,
        primary_metric="net_contribution",
        primary_value=120.50,
        normalized_reward=0.85,
        reward_version="v1",
        attribution_confidence=0.98,
        maturity="mature",
        quality_status="eligible",
        created_at=datetime.now(UTC),
    )

    feature_view = build_p10_feature_view(outcome)
    payload = feature_view.model_dump()

    # Assert payload satisfies consumer schema
    assert_seam_payload(payload, ConsumerP10Schema, seam="C8")
    assert payload["unit_id"] == "pkg_synth_01"
    assert payload["experiment_id"] == "exp_synth_01"
    assert payload["arm_id"] == "arm_synth_01"
    assert payload["primary_outcome"]["metric"] == "net_contribution"
    assert payload["quality"]["attribution_confidence"] == 0.98

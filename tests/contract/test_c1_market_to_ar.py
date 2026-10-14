# tests/contract/test_c1_market_to_ar.py
"""Seam C1 — PHASE-P1 → PHASE-P2 (market opportunity).

The producer is P1's real API payload (in-process, fixture-backed); the consumer
schema carries the registry's frozen field names verbatim. When this fails, the fix
is decided against the registry *first* (amend it via ADR), then applied to both
sides in one change — never by editing this file to match the producer.

Registry note this test pins: **`freshness` is not on the P1 wire.** P2 §46 derives
`freshness_seconds` from `as_of`, so the assertion here is that `as_of` is an aware
ISO-8601 instant and that no `freshness` key exists to be misread. That is the seam
entry's near-miss (`freshness_seconds` is P2-internal), resolved by derivation rather
than by inventing a field.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C1 · PHASE-P1 → PHASE-P2"

REGISTRY_FIELDS: tuple[str, ...] = (
    "arm_id",
    "market_opportunity_score",
    "confidence",
    "as_of",
    "taxonomy",
    "components",
    "evidence",
    "score_version",
)
REGISTRY_TAXONOMY_FIELDS: tuple[str, ...] = (
    "genre",
    "subgenre",
    "mood",
    "use_case",
    "language",
    "vocal_mode",
    "duration_bucket",
    "visual_world",
    "title_archetype",
)
REGISTRY_COMPONENT_FIELDS: tuple[str, ...] = (
    "demand",
    "growth",
    "competitor_velocity",
    "competition",
    "audience_quality",
    "cross_platform",
    "production_fit",
)
REGISTRY_EVIDENCE_FIELDS: tuple[str, ...] = (
    "observation_count",
    "source_count",
    "top_observation_ids",
)


class TaxonomySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    genre: str | None = None
    subgenre: str | None = None
    mood: list[str] = []
    use_case: list[str] = []
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    title_archetype: str | None = None


class ComponentsSchema(BaseModel):
    """Registry names verbatim; values are numbers-or-null (no fabricated zero, §3.6).

    The registry freezes the seven names; the fixture's three unobserved components
    (`growth`, `audience_quality`, `cross_platform`) pin the null case, because a
    fabricated 0.0 would read to P2 as a measured terrible score.
    """

    model_config = ConfigDict(extra="forbid")

    demand: float | None
    growth: float | None
    competitor_velocity: float | None
    competition: float | None
    audience_quality: float | None
    cross_platform: float | None
    production_fit: float | None


class EvidenceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_count: int
    source_count: int
    top_observation_ids: list[str]


class ConsumerSchema(BaseModel):
    """P2's expected schema, field names copied from the registry entry."""

    model_config = ConfigDict(extra="forbid")

    arm_id: str
    market_opportunity_score: float
    confidence: float
    as_of: datetime
    taxonomy: TaxonomySchema
    components: ComponentsSchema
    evidence: EvidenceSchema
    score_version: str


def test_declared_fields_match_the_consumer_schema() -> None:
    assert set(ConsumerSchema.model_fields) == set(REGISTRY_FIELDS)
    assert set(TaxonomySchema.model_fields) == set(REGISTRY_TAXONOMY_FIELDS)
    assert set(ComponentsSchema.model_fields) == set(REGISTRY_COMPONENT_FIELDS)
    assert set(EvidenceSchema.model_fields) == set(REGISTRY_EVIDENCE_FIELDS)


def test_producer_payload_validates_against_the_consumer_schema(c1_api_client) -> None:
    response = c1_api_client.get("/market/v1/opportunities", params={"limit": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"schema_version", "items", "next_cursor", "as_of", "score_version"}
    payload = json.loads(json.dumps(body["items"][0]))
    assert_seam_payload(
        payload, ConsumerSchema, seam=REGISTRY_ENTRY, producer="GET /market/v1/opportunities"
    )


def test_freshness_is_derivable_from_as_of_and_never_a_field(c1_api_client) -> None:
    payload = c1_api_client.get("/market/v1/opportunities", params={"limit": 1}).json()["items"][0]
    assert "freshness" not in payload and "freshness_seconds" not in payload
    assert datetime.fromisoformat(payload["as_of"]).tzinfo is not None, (
        "P2 derives freshness_seconds from as_of (registry C1); a naive instant would "
        "silently become a UTC assumption across phases"
    )


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists, "W0.10 created the registry; it cannot be missing"
    contract_registry.require_entry(REGISTRY_ENTRY)

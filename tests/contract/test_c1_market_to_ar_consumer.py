# tests/contract/test_c1_market_to_ar_consumer.py
"""Seam C1 — PHASE-P1 → PHASE-P2, P2's consumer half.

P1's producer half lives in `tests/contract/test_c1_market_to_ar.py` (P1.10) and is not
duplicated here. This file pins what P2 *consumes*: the frozen C1 field names, the flat
envelope, the no-`freshness` rule, and null-never-zero components — against P1's real
payload served by the fixture app. A rename on either side fails here by name.
"""

from __future__ import annotations

import json

from services.autonomous_ar.ingestion.p1_adapter import (
    EvidencePayload,
    OpportunityPayload,
    P1Client,
    TaxonomyPayload,
    adapt_opportunity,
)
from tests.support.c1_api import SEAM_TOKEN, SyncASGITransport, open_c1_client
from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C1 · PHASE-P1 → PHASE-P2"

REGISTRY_FIELDS: tuple[str, ...] = (
    "arm_id", "market_opportunity_score", "confidence", "as_of", "taxonomy", "components",
    "evidence", "score_version",
)
REGISTRY_TAXONOMY_FIELDS: tuple[str, ...] = (
    "genre", "subgenre", "mood", "use_case", "language", "vocal_mode", "duration_bucket",
    "visual_world", "title_archetype",
)
REGISTRY_COMPONENT_FIELDS: tuple[str, ...] = (
    "demand", "growth", "competitor_velocity", "competition", "audience_quality",
    "cross_platform", "production_fit",
)
REGISTRY_EVIDENCE_FIELDS: tuple[str, ...] = ("observation_count", "source_count",
                                             "top_observation_ids")


def test_consumer_schema_matches_the_registry_field_lists() -> None:
    assert set(OpportunityPayload.model_fields) == set(REGISTRY_FIELDS)
    assert set(TaxonomyPayload.model_fields) == set(REGISTRY_TAXONOMY_FIELDS)
    components_model = OpportunityPayload.model_fields["components"].annotation
    assert set(components_model.model_fields) == set(REGISTRY_COMPONENT_FIELDS)
    assert set(EvidencePayload.model_fields) == set(REGISTRY_EVIDENCE_FIELDS)


def test_real_producer_payload_validates_against_the_consumer_schema() -> None:
    with open_c1_client() as api:
        body = api.get("/market/v1/opportunities", params={"limit": 1}).json()
        assert set(body) == {"items", "next_cursor", "as_of", "score_version"}, (
            "ADR-0022: the wire is the registry's flat envelope, never Page/Envelope"
        )
        payload = json.loads(json.dumps(body["items"][0]))
        assert "freshness" not in payload and "freshness_seconds" not in payload
        assert_seam_payload(payload, OpportunityPayload, seam=REGISTRY_ENTRY,
                            producer="GET /market/v1/opportunities",
                            consumer="autonomous_ar.ingestion.p1_adapter.OpportunityPayload")


def test_adapter_consumes_only_frozen_fields() -> None:
    with open_c1_client() as api:
        client = P1Client(base_url="http://p1.test", token=SEAM_TOKEN,
                          transport=SyncASGITransport(api.app))
        page = client.fetch_opportunities(limit=1)
        arm = adapt_opportunity(page.items[0], now=page.as_of)
        assert "freshness_seconds" not in page.items[0].model_dump()
        assert arm.freshness_seconds == 0.0  # derived here, from as_of, not read off the wire


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists, "the registry was frozen 2026-09-20; it cannot be missing"
    contract_registry.require_entry(REGISTRY_ENTRY)

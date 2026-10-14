# tests/unit/market/test_api_schemas.py
"""P1.10: the frozen C1 wire shapes (field names, envelope, cursors)."""

import json
from datetime import UTC, datetime

from services.market_intelligence.api.schemas import (
    ComponentsOut,
    EvidenceOut,
    OpportunityListResponse,
    OpportunityOut,
    TaxonomyOut,
    decode_cursor,
    encode_cursor,
)


def test_the_opportunity_payload_carries_exactly_the_frozen_c1_field_names() -> None:
    payload = OpportunityOut.model_json_schema()["properties"]
    assert set(payload) == {
        "arm_id", "market_opportunity_score", "confidence", "as_of", "taxonomy",
        "components", "evidence", "score_version",
    }
    assert set(TaxonomyOut.model_json_schema()["properties"]) == {
        "genre", "subgenre", "mood", "use_case", "language", "vocal_mode",
        "duration_bucket", "visual_world", "title_archetype",
    }
    assert set(ComponentsOut.model_json_schema()["properties"]) == {
        "demand", "growth", "competitor_velocity", "competition", "audience_quality",
        "cross_platform", "production_fit",
    }
    assert set(EvidenceOut.model_json_schema()["properties"]) == {
        "observation_count", "source_count", "top_observation_ids",
    }


def test_the_list_envelope_is_the_registry_shape_not_a_page() -> None:
    response = OpportunityListResponse.build(items=[], next_cursor=None,
                                            as_of=datetime(2026, 9, 20, tzinfo=UTC),
                                            score_version="p1-v1")
    dumped = json.loads(response.model_dump_json())
    assert set(dumped) == {"schema_version", "items", "next_cursor", "as_of", "score_version"}
    assert "freshness" not in dumped


def test_cursors_round_trip_and_refuse_garbage() -> None:
    cursor = encode_cursor(82.4, "rnb_late_night_drive")
    assert decode_cursor(cursor) == (82.4, "rnb_late_night_drive")
    import pytest

    with pytest.raises(ValueError):
        decode_cursor("not-a-cursor")

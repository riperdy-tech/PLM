# tests/unit/ar/test_p1_adapter.py
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from services.autonomous_ar.ingestion.p1_adapter import (
    MarketUnavailable,
    P1Client,
    adapt_opportunity,
)
from tests.support.c1_api import AS_OF, SEAM_TOKEN, SyncASGITransport, open_c1_client

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "arm_id": "rnb_late_night_drive_english_vocal_45_75m",
        "market_opportunity_score": 82.0, "confidence": 0.7, "as_of": AS_OF.isoformat(),
        "taxonomy": {"genre": "rnb", "subgenre": None, "mood": ["melancholic", "dreamy"],
                     "use_case": ["late_night_drive"], "language": "english",
                     "vocal_mode": "vocal", "duration_bucket": "45_75m",
                     "visual_world": "rainy_asian_city", "title_archetype": "time_place_mood"},
        "components": {"demand": 70.0, "growth": None, "competitor_velocity": 55.0,
                       "competition": 60.0, "audience_quality": None, "cross_platform": None,
                       "production_fit": 58.0},
        "evidence": {"observation_count": 6, "source_count": 2, "top_observation_ids": ["o1"]},
        "score_version": "p1-v1",
    }
    base.update(overrides)
    return base


def test_mapping_is_exact_and_unobserved_components_stay_none() -> None:
    from services.autonomous_ar.ingestion.p1_adapter import OpportunityPayload

    arm = adapt_opportunity(OpportunityPayload.model_validate(_payload()), now=NOW)
    assert arm.arm_id == "rnb_late_night_drive_english_vocal_45_75m"
    assert arm.p1_score == 82.0 and arm.p1_confidence == 0.7
    assert arm.component_scores["demand"] == 70.0
    assert arm.component_scores["growth"] is None, "a null component is not a zero (C1→P2)"
    assert arm.measured_component_count == 4
    assert arm.observation_count == 6 and arm.source_count == 2
    assert arm.score_version == "p1-v1"


def test_freshness_is_derived_from_an_aware_as_of() -> None:
    from services.autonomous_ar.ingestion.p1_adapter import OpportunityPayload

    payload = OpportunityPayload.model_validate(_payload())
    arm = adapt_opportunity(payload, now=AS_OF + timedelta(hours=5))
    assert arm.freshness_seconds == pytest.approx(5 * 3600.0)
    # as_of is ahead of the caller's clock (skew): age clamps at zero, never negative.
    arm_early = adapt_opportunity(payload, now=AS_OF - timedelta(hours=1))
    assert arm_early.freshness_seconds == 0.0


def test_payload_schema_refuses_unknown_fields() -> None:
    from services.autonomous_ar.ingestion.p1_adapter import OpportunityPayload

    with pytest.raises(ValueError, match="freshness"):
        OpportunityPayload.model_validate(_payload(freshness="fresh"))


def test_client_reads_the_flat_c1_envelope_over_the_real_app() -> None:
    with open_c1_client() as api:
        client = P1Client(base_url="http://p1.test", token=SEAM_TOKEN,
                          transport=SyncASGITransport(api.app))
        page = client.fetch_opportunities(limit=5)
        assert page.score_version == "p1-v1"
        assert page.as_of.tzinfo is not None
        assert [item.arm_id for item in page.items] == [
            "rnb_late_night_drive_english_vocal_45_75m"
        ]
        arm = adapt_opportunity(page.items[0], now=page.as_of)
        assert arm.p1_score == page.items[0].market_opportunity_score


def test_wrong_token_is_market_unavailable_not_empty_data() -> None:
    with open_c1_client() as api:
        client = P1Client(base_url="http://p1.test", token="wrong-token",
                          transport=SyncASGITransport(api.app))
        with pytest.raises(MarketUnavailable, match="401"):
            client.fetch_opportunities()


def test_transport_failure_is_market_unavailable() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = P1Client(base_url="http://p1.test", token="t",
                      transport=httpx.MockTransport(_boom))
    with pytest.raises(MarketUnavailable, match="connection refused"):
        client.fetch_opportunities()


def test_no_token_configured_is_refused_before_any_request() -> None:
    client = P1Client(base_url="http://p1.test", token=None)
    with pytest.raises(MarketUnavailable, match="PLM_SERVICE_TOKEN"):
        client.fetch_opportunities()


def test_adapter_never_imports_a_database_or_touches_a_table() -> None:
    from pathlib import Path

    source = Path("services/autonomous_ar/ingestion/p1_adapter.py").read_text(encoding="utf-8")
    assert "sqlalchemy" not in source, "AC-09/R15: P2 reads P1 over HTTP only"
    assert "market." not in source, "the adapter must not reference P1's schema"

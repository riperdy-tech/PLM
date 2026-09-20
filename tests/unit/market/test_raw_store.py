# tests/unit/market/test_raw_store.py
from __future__ import annotations

from datetime import UTC, datetime

from services.market_intelligence.domain.entities import CollectionRunCounters
from services.market_intelligence.domain.enums import (
    CollectionRunStatus,
    ConnectorType,
    ProviderName,
)
from services.market_intelligence.storage import repositories

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_upsert_source_is_idempotent_on_the_provider_name(market_session) -> None:
    first = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API,
        base_identifier="https://www.googleapis.com/youtube/v3",
        configuration_version="p1-sources-v1",
    )
    second = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API,
        base_identifier="https://www.googleapis.com/youtube/v3",
        configuration_version="p1-sources-v1",
    )
    assert first == second


def test_collection_run_records_status_then_counters_and_costs(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None,
        software_version="p1-v1", started_at=FIXED_NOW,
    )
    repositories.add_run_cost(market_session, run_id, api_calls=3, storage_bytes=2048)
    repositories.finish_collection_run(
        market_session, run_id, status=CollectionRunStatus.PARTIAL,
        counters=CollectionRunCounters(request_count=3, success_count=2, error_count=1,
                                       raw_observation_count=2, normalized_observation_count=1),
        error_summary={"RATE_LIMIT": 1}, finished_at=FIXED_NOW,
    )
    run = repositories.load_run(market_session, run_id)
    assert run is not None
    assert run["status"] == "partial"
    assert run["cost_api_calls"] == 3
    assert run["cost_storage_bytes"] == 2048
    assert run["error_summary"] == {"RATE_LIMIT": 1}
    assert run["finished_at"] is not None

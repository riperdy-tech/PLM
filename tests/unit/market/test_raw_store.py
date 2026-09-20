# tests/unit/market/test_raw_store.py
from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from plm_platform.storage import ObjectAlreadyExistsError, StoredObject, sha256_digest

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.entities import CollectionRunCounters
from services.market_intelligence.domain.enums import (
    CollectionRunStatus,
    ConnectorType,
    ProviderName,
    ProviderObjectType,
)
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.raw_store import store_raw_observation

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


PAYLOAD = {"kind": "youtube#video", "id": "vid000000001",
           "statistics": {"viewCount": "120000"}}
CONTEXT_HASH = "a" * 64


class FakeObjectStore:
    """Records puts; raises the real ObjectAlreadyExistsError on a repeated key."""

    def __init__(self) -> None:
        self.keys: dict[str, bytes] = {}
        self.calls: list[str] = []

    def __call__(self, prefix, resource_path, data, *, content_type="application/octet-stream",
                 settings=None, client=None) -> StoredObject:
        digest = sha256_digest(data)
        key = f"{prefix}{resource_path}/{digest}"
        self.calls.append(key)
        if key in self.keys:
            raise ObjectAlreadyExistsError(f"refusing to overwrite {key!r}")
        self.keys[key] = data
        return StoredObject(key=key, digest=digest, size=len(data))


def _store(market_session, store, *, observed_at=FIXED_NOW):
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None,
        software_version="p1-v1", started_at=FIXED_NOW,
    )
    fetched = FetchedObject(
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid000000001",
        observed_at=observed_at, payload=PAYLOAD,
    )
    return source_id, run_id, store_raw_observation(
        market_session, run_id=run_id, source_id=source_id, fetched=fetched,
        request_context_hash=CONTEXT_HASH, put_object=store,
    )


def test_raw_observation_is_immutable_content_addressed_and_costed(market_session) -> None:
    store = FakeObjectStore()
    _, run_id, stored = _store(market_session, store)
    assert stored.created is True
    assert stored.payload_uri.startswith("plm://")
    assert stored.payload_uri.endswith(stored.payload_hash)
    assert "market/raw/youtube_data_api/video/2026-09-20/" in store.calls[0]
    assert store.keys[store.calls[0]] == (
        b'{"id":"vid000000001","kind":"youtube#video","statistics":{"viewCount":"120000"}}'
    )
    run = repositories.load_run(market_session, run_id)
    assert run["cost_storage_bytes"] == len(store.keys[store.calls[0]])
    row = market_session.execute(
        sa.text("SELECT payload_hash, request_context_hash, schema_version "
                "FROM market.market_raw_observations")
    ).one()
    assert row.schema_version == "p1-raw-v1"
    assert row.request_context_hash == CONTEXT_HASH
    assert row.payload_hash == stored.payload_hash


def test_storing_the_same_observation_twice_is_idempotent(market_session) -> None:
    store = FakeObjectStore()
    _, _, first = _store(market_session, store)
    _, _, second = _store(market_session, store)
    assert second.created is False
    assert second.raw_observation_id == first.raw_observation_id
    rows = market_session.execute(
        sa.text("SELECT count(*) FROM market.market_raw_observations")
    ).scalar_one()
    assert rows == 1


def test_a_later_collection_at_a_new_time_is_a_second_observation(market_session) -> None:
    store = FakeObjectStore()
    _, _, first = _store(market_session, store)
    _, _, second = _store(market_session, store,
                          observed_at=datetime(2026, 9, 21, 6, 0, tzinfo=UTC))
    assert second.created is True
    assert second.raw_observation_id != first.raw_observation_id

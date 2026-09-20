# tests/unit/market/test_daily_run_quota.py
"""§27's daily ceiling at the run level: overspending is refused or reduced, never done.

`_run_due_collection` is the seam between the scheduler's daily ledger and the
connector's per-run `QuotaBudget`; these tests drive it directly (the same session and
connector-factory a `run_daily` cycle passes through) with no network and no database
server: a refused run must make zero HTTP attempts, and a reduced run must stop at the
day's remainder.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from plm_platform.config import Secret
from plm_platform.storage import StoredObject, sha256_digest

from services.market_intelligence.cli import _run_due_collection
from services.market_intelligence.collection.rate_limit import QuotaBudget, RequestBudget
from services.market_intelligence.collection.scheduler import daily_quota_state, due_queries
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.connectors.youtube import (
    BASE_URL,
    YouTubeCredentials,
    YouTubeDataAPIConnector,
)
from services.market_intelligence.domain.enums import ConnectorType, ProviderName, QueryType
from services.market_intelligence.storage import repositories
from tests.support.http import MockHTTP

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"


class _MemoryObjects:
    """The object-store half of `store_raw_observation`, in memory (no MinIO here)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, prefix, resource_path, data, *, content_type="application/octet-stream",
                 settings=None, client=None) -> StoredObject:
        digest = sha256_digest(data)
        key = f"{prefix}{resource_path}/{digest}"
        self.calls.append(key)
        return StoredObject(key=key, digest=digest, size=len(data))


@pytest.fixture
def memory_objects(monkeypatch: pytest.MonkeyPatch) -> _MemoryObjects:
    store = _MemoryObjects()
    monkeypatch.setattr("services.market_intelligence.storage.raw_store.put_content", store)
    return store


def _install(market_session) -> str:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    repositories.upsert_query(
        market_session, source_id=source_id, query_text="lofi", query_type=QueryType.MUSIC_GENRE,
        locale="en", country_code="US", language="english", seed_group="bootstrap",
        priority=50, query_version="p1-queries-v1",
    )
    return source_id


def _book_spend(market_session, *, units: int, at: datetime | None = None) -> None:
    """Write the day's ledger the way a finished run does.

    `start_collection_run` ids are derived from (source, query, version, instant), so an
    earlier instant keeps the booking a *different* run from the one under test.
    """
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None, software_version="p1-v1",
        started_at=NOW - timedelta(hours=1) if at is None else at,
    )
    repositories.add_run_cost(market_session, run_id, api_quota_units=units)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _connector(mock: MockHTTP, *, units: int) -> YouTubeDataAPIConnector:
    return YouTubeDataAPIConnector(
        client=mock.client(base_url=BASE_URL), credentials=YouTubeCredentials(
            api_key=Secret("unit-test")),
        quota=QuotaBudget(units), request_budget=RequestBudget(50),
    )


def test_a_spent_day_refuses_the_run_without_a_single_http_attempt(market_session) -> None:
    _install(market_session)
    _book_spend(market_session, units=SETTINGS.quota_units_per_day)
    mock = MockHTTP()  # empty queue: any request would raise
    factory_calls: list[int] = []

    def factory(settings, units):  # pragma: no cover - must never run
        factory_calls.append(units)
        return _connector(mock, units=units)

    result = _run_due_collection(market_session, now=NOW, settings=SETTINGS,
                                 connector_factory=factory)

    assert result["quota_exhausted"] is True
    assert result["reason"] == "DAILY_QUOTA_EXHAUSTED"
    assert result["remaining_units"] == 0
    assert factory_calls == []  # no connector was even constructed
    assert len(mock.requests) == 0  # and no HTTP attempt was made
    # the refused queries are rescheduled (§27), not dropped and not hammered
    assert due_queries(market_session, now=NOW, settings=SETTINGS) == []
    row = repositories.load_queries(market_session)[0]
    assert row["next_run_at"] is not None


def test_a_thin_day_reduces_the_budget_and_the_run_stops_at_the_remainder(
    market_session, memory_objects
) -> None:
    _install(market_session)
    _book_spend(market_session, units=SETTINGS.quota_units_per_day - 100)
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    seen_units: list[int] = []

    def factory(settings, units):
        seen_units.append(units)
        return _connector(mock, units=units)

    result = _run_due_collection(market_session, now=NOW, settings=SETTINGS,
                                 connector_factory=factory)

    assert seen_units == [100]  # min(per-run 1000, day remaining 100) — reduced, not raised
    assert result["quota_exhausted"] is True
    assert result["status"] == "partial"
    # the day's ledger ends exactly at the ceiling — the 100-unit search was the last call
    state = daily_quota_state(market_session, now=NOW, settings=SETTINGS)
    assert state.used_units == SETTINGS.quota_units_per_day
    assert state.remaining_units == 0
    run = repositories.load_run(market_session, result["run_id"])
    assert run is not None and run["cost_api_quota_units"] == 100
    assert len(mock.requests) == 1  # the hydration call that would have crossed it never left


def test_a_day_with_room_runs_and_charges_the_ledger(market_session, memory_objects) -> None:
    _install(market_session)
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))

    result = _run_due_collection(market_session, now=NOW, settings=SETTINGS,
                                 connector=_connector(mock, units=SETTINGS.quota_units_per_run))

    assert result["status"] == "succeeded"
    run = repositories.load_run(market_session, result["run_id"])
    assert run is not None
    # one 100-unit search + one 1-unit hydration list call, charged to the day
    assert run["cost_api_quota_units"] == 101
    state = daily_quota_state(market_session, now=NOW, settings=SETTINGS)
    assert state.used_units == 101
    assert state.remaining_units == SETTINGS.quota_units_per_day - 101


def test_the_ledger_ignores_previous_days(market_session) -> None:
    _install(market_session)
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None, software_version="p1-v1",
        started_at=NOW - timedelta(days=1),
    )
    repositories.add_run_cost(market_session, run_id, api_quota_units=SETTINGS.quota_units_per_day)
    assert daily_quota_state(market_session, now=NOW, settings=SETTINGS).remaining_units == \
        SETTINGS.quota_units_per_day

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
from typing import Any, cast

import pytest
from market_intelligence.cli import _run_due_collection
from market_intelligence.collection.rate_limit import QuotaBudget, RequestBudget
from market_intelligence.collection.scheduler import daily_quota_state, due_queries
from market_intelligence.config import MarketSettings, load_market_settings
from market_intelligence.connectors.youtube import (
    BASE_URL,
    YouTubeCredentials,
    YouTubeDataAPIConnector,
)
from market_intelligence.domain.enums import ConnectorType, ProviderName, QueryType
from market_intelligence.storage import repositories
from plm_platform.config import Secret
from plm_platform.storage import StoredObject, sha256_digest
from sqlalchemy.orm import Session

from tests.support.http import MockHTTP

# Exercises code that loads platform settings; the values are set explicitly.
pytestmark = pytest.mark.usefixtures("platform_env")

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"


class _MemoryObjects:
    """The object-store half of `store_raw_observation`, in memory (no MinIO here)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, prefix: str, resource_path: str, data: bytes, *,
                 content_type: str = "application/octet-stream",
                 settings: object = None, client: object = None) -> StoredObject:
        digest = sha256_digest(data)
        key = f"{prefix}{resource_path}/{digest}"
        self.calls.append(key)
        return StoredObject(key=key, digest=digest, size=len(data))


@pytest.fixture
def memory_objects(monkeypatch: pytest.MonkeyPatch) -> _MemoryObjects:
    store = _MemoryObjects()
    monkeypatch.setattr("market_intelligence.storage.raw_store.put_content", store)
    return store


def _install(market_session: Session) -> str:
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


def _book_spend(market_session: Session, *, units: int, at: datetime | None = None) -> None:
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


def _fixture(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _connector(mock: MockHTTP, *, units: int) -> YouTubeDataAPIConnector:
    return YouTubeDataAPIConnector(
        client=mock.client(base_url=BASE_URL), credentials=YouTubeCredentials(
            api_key=Secret("unit-test")),
        quota=QuotaBudget(units), request_budget=RequestBudget(50),
    )


def test_a_spent_day_refuses_the_run_without_a_single_http_attempt(market_session: Session) -> None:
    _install(market_session)
    _book_spend(market_session, units=SETTINGS.quota_units_per_day)
    mock = MockHTTP()  # empty queue: any request would raise
    factory_calls: list[int] = []

    def factory(settings: MarketSettings, units: int) -> YouTubeDataAPIConnector:  # pragma: no cover - must never run
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
    market_session: Session, memory_objects: _MemoryObjects
) -> None:
    _install(market_session)
    _book_spend(market_session, units=SETTINGS.quota_units_per_day - 100)
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    seen_units: list[int] = []

    def factory(settings: MarketSettings, units: int) -> YouTubeDataAPIConnector:
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


def test_a_day_with_room_runs_and_charges_the_ledger(market_session: Session, memory_objects: _MemoryObjects) -> None:
    _install(market_session)
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    mock.enqueue_json(_fixture("channels_lofi_batch.json"))

    result = _run_due_collection(market_session, now=NOW, settings=SETTINGS,
                                 connector=_connector(mock, units=SETTINGS.quota_units_per_run))

    assert result["status"] == "succeeded"
    # ADR-0044's English-first market reaches the search request from settings
    mock.assert_query_params(0, regionCode="US", relevanceLanguage="en")
    run = repositories.load_run(market_session, result["run_id"])
    assert run is not None
    # one 100-unit search + 1-unit videos.list + 1-unit channels.list, charged to the day
    assert run["cost_api_quota_units"] == 102
    state = daily_quota_state(market_session, now=NOW, settings=SETTINGS)
    assert state.used_units == 102
    assert state.remaining_units == SETTINGS.quota_units_per_day - 102


def test_the_ledger_ignores_previous_days(market_session: Session) -> None:
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


def test_a_run_cut_short_keeps_the_queries_it_finished_and_moves_on_next_time(
    market_session: Session, memory_objects: _MemoryObjects
) -> None:
    """2026-11-10: every daily run hit the per-run budget and rescheduled every query,
    finished ones included, so the next run began at the same head again and the pool's
    tail (the focus sub-style seeds) never ran. The finished queries now count as run."""
    source_id = _install(market_session)
    repositories.upsert_query(
        market_session, source_id=source_id, query_text="piano and synth focus",
        query_type=QueryType.MUSIC_GENRE, locale="en", country_code="US", language="english",
        seed_group="bootstrap", priority=40, query_version="p1-queries-v1",
    )
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    mock.enqueue_json(_fixture("channels_lofi_batch.json"))

    # 150 units: the first query (100 + 1 + 1) fits, the second search (100) does not.
    result = _run_due_collection(market_session, now=NOW, settings=SETTINGS,
                                 connector=_connector(mock, units=150))

    assert result["quota_exhausted"] is True
    rows = {r["query_text"]: r for r in repositories.load_queries(market_session)}
    assert rows["lofi"]["last_run_at"] is not None  # it ran
    assert rows["piano and synth focus"]["last_run_at"] is None  # it waits for the next run
    run = repositories.load_run(market_session, result["run_id"])
    assert run is not None and run["error_summary"] == {"QUOTA_EXCEEDED": 1}

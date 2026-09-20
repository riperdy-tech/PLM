# tests/unit/market/test_scheduler.py
"""P1.11: what is due, when each query runs next, and the §27 quota responses."""

from datetime import UTC, datetime, timedelta

import pytest

from services.market_intelligence.collection.rate_limit import QuotaExceededError
from services.market_intelligence.collection.scheduler import (
    daily_quota_state,
    due_queries,
    mark_run,
    mark_scheduled,
    reset_schedule,
    run_quota_budget,
)
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.enums import ConnectorType, ProviderName, QueryType
from services.market_intelligence.storage import repositories

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


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


def test_a_never_run_query_is_due_and_gets_a_cadence_back(market_session) -> None:
    _install(market_session)
    due = due_queries(market_session, now=NOW, settings=SETTINGS)
    assert [state.query_text for state in due] == ["lofi"]
    scheduled = mark_scheduled(market_session, due, now=NOW, settings=SETTINGS)
    assert scheduled == 1
    row = repositories.load_queries(market_session)[0]
    assert row["next_run_at"] is not None and row["next_run_at"] > NOW
    assert due_queries(market_session, now=NOW, settings=SETTINGS) == []


def test_a_quota_reset_reschedules_with_a_reason_and_reopens_the_query(market_session) -> None:
    _install(market_session)
    due = due_queries(market_session, now=NOW, settings=SETTINGS)
    mark_scheduled(market_session, due, now=NOW, settings=SETTINGS)
    reset_schedule(market_session, due[0].query_id, now=NOW, settings=SETTINGS,
                   reason="QUOTA_EXCEEDED")
    assert due_queries(market_session, now=NOW, settings=SETTINGS) == []
    mark_run(market_session, due[0].query_id, at=NOW + timedelta(hours=1))
    row = repositories.load_queries(market_session)[0]
    assert row["last_run_at"] is not None


# -- §27 daily quota ceiling: the connector's budget is per-run, the day's is not --------


def _spend(market_session, *, units: int, at: datetime = NOW) -> None:
    """Book quota units against one day the way a finished run does."""
    source_id = _install(market_session)
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None, software_version="p1-v1",
        started_at=at,
    )
    repositories.add_run_cost(market_session, run_id, api_quota_units=units)


def test_the_day_ceiling_refuses_a_run_once_the_day_is_spent(market_session) -> None:
    _spend(market_session, units=SETTINGS.quota_units_per_day)
    state = daily_quota_state(market_session, now=NOW, settings=SETTINGS)
    assert state.used_units == SETTINGS.quota_units_per_day
    assert state.remaining_units == 0
    # nothing left today: the scheduler refuses the run (no connector is constructed)
    assert run_quota_budget(state, per_run_limit=SETTINGS.quota_units_per_run) is None


def test_a_thin_day_reduces_the_run_budget_instead_of_overspending(market_session) -> None:
    _spend(market_session, units=SETTINGS.quota_units_per_day - 100)
    state = daily_quota_state(market_session, now=NOW, settings=SETTINGS)
    assert state.remaining_units == 100
    budget = run_quota_budget(state, per_run_limit=SETTINGS.quota_units_per_run)
    assert budget is not None and budget.limit_units == 100  # reduced below the per-run cap
    budget.consume(100, operation="search.list")
    with pytest.raises(QuotaExceededError):  # a second search would cross the day's ceiling
        budget.consume(100, operation="search.list")
    assert budget.used == 100  # the day's remaining budget, and never more


def test_yesterdays_spend_does_not_charge_todays_budget(market_session) -> None:
    _spend(market_session, units=SETTINGS.quota_units_per_day,
           at=NOW - timedelta(days=1))
    state = daily_quota_state(market_session, now=NOW, settings=SETTINGS)
    assert state.used_units == 0
    assert run_quota_budget(state, per_run_limit=SETTINGS.quota_units_per_run) is not None

from datetime import UTC, datetime, timedelta

from services.market_intelligence.collection.query_planner import (
    QueryState,
    next_run_at,
    plan_refresh,
    priority_score,
    staleness_hours,
)
from services.market_intelligence.config import load_market_settings

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


def _state(**overrides) -> QueryState:
    base = {
        "query_id": "q1", "query_fingerprint": "f" * 8, "query_text": "lofi",
        "priority": 50, "seed_group": "bootstrap", "last_run_at": None,
        "next_run_at": None, "active": True,
    }
    base.update(overrides)
    return QueryState(**base)


def test_staleness_is_none_until_the_query_has_run() -> None:
    assert staleness_hours(_state(), now=NOW) is None
    assert staleness_hours(_state(last_run_at=NOW - timedelta(hours=30)), now=NOW) == 30.0


def test_priority_rises_with_staleness_and_importance_and_is_bounded() -> None:
    fresh = priority_score(_state(last_run_at=NOW), now=NOW, settings=SETTINGS)
    stale = priority_score(_state(last_run_at=NOW - timedelta(days=10)), now=NOW, settings=SETTINGS)
    important = priority_score(_state(priority=100, last_run_at=NOW - timedelta(days=10)),
                               now=NOW, settings=SETTINGS)
    assert 0.0 <= fresh < stale <= important <= 100.0


def test_cadence_is_configuration_driven_not_a_literal() -> None:
    high = _state(priority=95, last_run_at=NOW)
    normal = _state(priority=55, last_run_at=NOW)
    tail = _state(priority=10, last_run_at=NOW)
    assert next_run_at(high, now=NOW, settings=SETTINGS) == NOW + timedelta(hours=24)
    assert next_run_at(normal, now=NOW, settings=SETTINGS) == NOW + timedelta(hours=72)
    assert next_run_at(tail, now=NOW, settings=SETTINGS) == NOW + timedelta(hours=168)
    custom = load_market_settings(cadence=SETTINGS.cadence.__class__(high_priority=6.0))
    assert next_run_at(high, now=NOW, settings=custom) == NOW + timedelta(hours=6)


def test_plan_refresh_returns_due_queries_highest_priority_first() -> None:
    never_run = _state(query_id="new", priority=90, next_run_at=NOW - timedelta(hours=1))
    overdue = _state(query_id="old", priority=60, next_run_at=NOW - timedelta(days=2))
    not_due = _state(query_id="later", priority=99, next_run_at=NOW + timedelta(hours=5))
    planned = plan_refresh([not_due, overdue, never_run], now=NOW, settings=SETTINGS)
    assert [state.query_id for state in planned] == ["new", "old"]


def test_an_inactive_query_is_never_planned() -> None:
    assert plan_refresh([_state(active=False, next_run_at=NOW - timedelta(days=1))],
                        now=NOW, settings=SETTINGS) == []

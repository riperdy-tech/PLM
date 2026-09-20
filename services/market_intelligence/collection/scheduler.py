# services/market_intelligence/collection/scheduler.py
"""§9/§26/§27: what is due, when each query runs next, and the daily quota ledger.

The cadence decision is a pure function (`query_planner`); this module is the only
place that reads/writes `next_run_at`/`last_run_at` and the only place that decides
how much provider quota one scheduled run may spend. The per-run cap lives in the
connector's `QuotaBudget`; the per-day **ceiling** lives here, because the scheduled
run — not the connector — is what survives across runs of the same day (§27).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..storage.repositories import (
    load_queries,
    sum_run_quota_units,
    update_query_schedule,
)
from .query_planner import QueryState, next_run_at, plan_refresh
from .rate_limit import QuotaBudget

__all__ = [
    "DailyQuotaState",
    "daily_quota_state",
    "due_queries",
    "mark_run",
    "mark_scheduled",
    "reset_schedule",
    "run_quota_budget",
]


@dataclass(frozen=True, slots=True)
class DailyQuotaState:
    """Provider quota units spent so far today (UTC) against the day's ceiling."""

    day_start: datetime
    limit_units: int
    used_units: int

    @property
    def remaining_units(self) -> int:
        return max(0, self.limit_units - self.used_units)


def due_queries(session: Session, *, now: datetime, settings: MarketSettings) -> list[QueryState]:
    """Active queries whose `next_run_at` has arrived, highest priority first."""
    states = [_state(row) for row in load_queries(session, active_only=True)]
    return plan_refresh(states, now=now, settings=settings)


def mark_scheduled(session: Session, states: list[QueryState], *, now: datetime,
                   settings: MarketSettings) -> int:
    """Set the next cadence for the queries just planned; returns how many moved."""
    for state in states:
        update_query_schedule(
            session, query_id=state.query_id, now=now,
            next_run_at=next_run_at(state, now=now, settings=settings),
            last_run_at=None,
        )
    return len(states)


def mark_run(session: Session, query_id: str, *, at: datetime) -> None:
    """Record that a collection ran, without touching the next cadence."""
    update_query_schedule(session, query_id=query_id, now=at, next_run_at=None, last_run_at=at)


def reset_schedule(session: Session, query_id: str, *, now: datetime,
                   settings: MarketSettings, reason: str) -> None:
    """After quota exhaustion or a hard failure: reschedule instead of retrying (§23/§27).

    The priority class still decides the next window, so a quota-blocked high-priority
    query comes back before a long-tail one. `reason` is the caller's log token
    (`QUOTA_EXCEEDED`, `DAILY_QUOTA_EXHAUSTED`, …) — recorded by the caller, not here.
    """
    state = QueryState(query_id=query_id, query_fingerprint="", query_text="", priority=100,
                       seed_group="bootstrap", last_run_at=None, next_run_at=None, active=True)
    update_query_schedule(
        session, query_id=query_id, now=now,
        next_run_at=next_run_at(state, now=now, settings=settings), last_run_at=None,
    )
    _ = reason  # the reason is logged by the caller (`observability.logging`)


def daily_quota_state(session: Session, *, now: datetime,
                      settings: MarketSettings) -> DailyQuotaState:
    """Units spent by runs started today (UTC) against `settings.quota_units_per_day`.

    Every run records the units its connector charged (`cost_api_quota_units`), so the
    ledger survives a process restart — a per-run budget alone cannot stop a day of
    replays from overspending §27's daily allowance.
    """
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = sum_run_quota_units(session, since=day_start, until=day_start + timedelta(days=1))
    return DailyQuotaState(day_start=day_start, limit_units=settings.quota_units_per_day,
                           used_units=used)


def run_quota_budget(state: DailyQuotaState, *, per_run_limit: int) -> QuotaBudget | None:
    """The budget one run may spend: `min(per-run, remaining day)`, or `None` refused.

    `None` means the day's ceiling is already reached — the scheduler refuses the run
    and reschedules the queries (they keep their cadence; §27 says preserve quota for
    priority queries rather than hammer the provider). Otherwise the connector is
    constructed with this budget, so an attempt that would cross the day's ceiling
    raises `QuotaExceededError` *before* the HTTP call instead of overspending.
    """
    if state.remaining_units <= 0:
        return None
    return QuotaBudget(min(per_run_limit, state.remaining_units))


def _state(row) -> QueryState:
    return QueryState(
        query_id=str(row["id"]), query_fingerprint=str(row["query_fingerprint"]),
        query_text=str(row["query_text"]), priority=int(row["priority"]),
        seed_group=str(row["seed_group"]), last_run_at=row["last_run_at"],
        next_run_at=row["next_run_at"], active=bool(row["active"]),
    )

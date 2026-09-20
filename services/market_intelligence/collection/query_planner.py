# services/market_intelligence/collection/query_planner.py
"""§9: schedule queries by expected information value.

Pure functions over `QueryState` — no database, no clock access, no randomness, so
the planner is unit-testable and the Temporal layer (P1.11) only wires it up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import MarketSettings

__all__ = ["QueryState", "next_run_at", "plan_refresh", "priority_score", "staleness_hours"]

_STALENESS_CAP_HOURS = 720.0  # 30 days: past this, more staleness adds no urgency
# A never-refreshed query scores 0.5 * importance * 100; both class boundaries are
# §9's 80/30 priority classes seen through that blend at zero staleness (40 = 0.5*80,
# 15 = 0.5*30), so falling behind on the calendar only ever promotes a class.
_HIGH_PRIORITY_AT = 40.0
_NORMAL_AT = 15.0


@dataclass(frozen=True, slots=True)
class QueryState:
    """The planner's whole input: identity, importance and the last/next run."""

    query_id: str
    query_fingerprint: str
    query_text: str
    priority: int
    seed_group: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    active: bool


def staleness_hours(state: QueryState, *, now: datetime) -> float | None:
    """Hours since the last run; `None` means "never run" — not zero staleness."""
    if state.last_run_at is None:
        return None
    return (now - state.last_run_at).total_seconds() / 3600


def priority_score(state: QueryState, *, now: datetime, settings: MarketSettings) -> float:
    """§9's factors, in one bounded 0–100 number.

    Staleness (never-run counts as maximally stale) and the stored importance drive
    it; a long-tail seed group is discounted so discovery never crowds out refresh.
    """
    stale = staleness_hours(state, now=now)
    stale_component = 1.0 if stale is None else min(stale, _STALENESS_CAP_HOURS) / _STALENESS_CAP_HOURS
    importance = min(max(state.priority, 0), 100) / 100.0
    group_weight = 0.6 if state.seed_group == "long_tail" else 1.0
    return round(100.0 * group_weight * (0.5 * stale_component + 0.5 * importance), 6)


def next_run_at(
    state: QueryState, *, now: datetime, settings: MarketSettings, score: float | None = None,
) -> datetime:
    """Cadence by priority class, all three windows from `settings.cadence` (§9/§26)."""
    resolved = priority_score(state, now=now, settings=settings) if score is None else score
    if resolved >= _HIGH_PRIORITY_AT:
        hours = settings.cadence.high_priority
    elif resolved >= _NORMAL_AT:
        hours = settings.cadence.normal
    else:
        hours = settings.cadence.long_tail
    return now + timedelta(hours=hours)


def plan_refresh(
    states: list[QueryState], *, now: datetime, settings: MarketSettings
) -> list[QueryState]:
    """Active queries whose `next_run_at` has arrived (or was never set), importance first."""
    due: list[QueryState] = []
    for state in states:
        if not state.active:
            continue
        if state.next_run_at is not None and state.next_run_at > now:
            continue
        due.append(state)
    return sorted(
        due,
        key=lambda state: (-priority_score(state, now=now, settings=settings),
                           state.query_fingerprint),
    )

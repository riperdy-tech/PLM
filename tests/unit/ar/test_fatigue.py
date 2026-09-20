# tests/unit/ar/test_fatigue.py
"""§26: fatigue is a decayed function of stored history and the policy version — every
function takes its `now` (the cycle instant the caller persists), never a wall clock."""
from datetime import UTC, datetime, timedelta

from services.autonomous_ar.domain.policy import FatigueConfig
from services.autonomous_ar.modeling.fatigue import (
    exposure_score,
    fatigue_penalty,
    is_in_cooldown,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
CONFIG = FatigueConfig(decay_half_life_days=14.0, max_penalty=0.5, cooldown_hours=72.0)


def test_recent_exposure_raises_the_penalty_and_old_exposure_forgets_it() -> None:
    fresh = exposure_score([NOW - timedelta(days=1)], now=NOW, half_life_days=14.0)
    stale = exposure_score([NOW - timedelta(days=28)], now=NOW, half_life_days=14.0)
    assert fresh > stale
    assert fatigue_penalty(fresh, config=CONFIG) > fatigue_penalty(stale, config=CONFIG)
    assert fatigue_penalty(0.0, config=CONFIG) == 0.0
    assert fatigue_penalty(100.0, config=CONFIG) == CONFIG.max_penalty


def test_the_penalty_halves_over_the_half_life() -> None:
    score_now = exposure_score([NOW], now=NOW, half_life_days=14.0)
    score_later = exposure_score([NOW - timedelta(days=14)], now=NOW, half_life_days=14.0)
    assert score_later == score_now / 2


def test_cooldown_window_is_explicit() -> None:
    assert is_in_cooldown(NOW - timedelta(hours=71), now=NOW, config=CONFIG) is True
    # both sides of the boundary: still cooling at 71h, selectable again at exactly 72h on
    assert is_in_cooldown(NOW - timedelta(hours=72), now=NOW, config=CONFIG) is False
    assert is_in_cooldown(NOW - timedelta(hours=73), now=NOW, config=CONFIG) is False
    assert is_in_cooldown(None, now=NOW, config=CONFIG) is False


def test_the_penalty_is_bounded_and_never_a_permanent_lockout() -> None:
    selected_at = NOW - timedelta(days=90)
    assert fatigue_penalty(1e9, config=CONFIG) == CONFIG.max_penalty  # bounded by max_penalty
    at_selection = exposure_score([selected_at], now=selected_at, half_life_days=14.0)
    much_later = exposure_score([selected_at], now=NOW, half_life_days=14.0)
    penalty_then = fatigue_penalty(at_selection, config=CONFIG)
    penalty_later = fatigue_penalty(much_later, config=CONFIG)
    # the decay itself re-opens the arm: no step ever "un-penalises" it, the weight just shrinks
    assert 0.0 < penalty_later < penalty_then <= CONFIG.max_penalty
    assert fatigue_penalty(much_later, config=FatigueConfig(enabled=False)) == 0.0

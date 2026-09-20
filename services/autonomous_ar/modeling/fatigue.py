# services/autonomous_ar/modeling/fatigue.py
"""Exposure, fatigue and cooldown (§26): a temporary penalty that decays by construction.

The penalty is a function of a decayed exposure score, so it forgets old selections
without ever needing an explicit "un-penalise" step — §26's "do not use a permanent
penalty" holds structurally, not by convention. Nothing here reads a clock: `now` is the
caller's persisted instant (the cycle's `as_of`), so replaying stored history recomputes
identical numbers. The curve's knee is the module constant below, and it moves only with
`policy_version` (`PolicyConfig.version`): changing it without a version bump would change
selection behaviour silently, which ADR-0023's versioning rule forbids.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from ..domain.policy import FatigueConfig

__all__ = ["exposure_score", "fatigue_penalty", "is_in_cooldown"]

SCORE_AT_MAX_PENALTY = 4.0
"""Exposure score at which the configured maximum penalty binds (invented constant, flagged
in the plan's values list #14; it moves only with `policy_version`)."""


def exposure_score(selection_instants: Sequence[datetime], *, now: datetime,
                   half_life_days: float) -> float:
    """Decayed count of recent selections; future instants count as 'just now'."""
    if half_life_days <= 0.0:
        raise ValueError("half_life_days must be > 0")
    score = 0.0
    for instant in selection_instants:
        age_days = max(0.0, (now - instant).total_seconds() / 86400.0)
        score += 0.5 ** (age_days / half_life_days)
    return score


def fatigue_penalty(score: float, *, config: FatigueConfig) -> float:
    """Bounded, monotone in recent exposure; zero when disabled or nothing is recent."""
    if not config.enabled or score <= 0.0:
        return 0.0
    return config.max_penalty * min(1.0, score / SCORE_AT_MAX_PENALTY)


def is_in_cooldown(last_selected_at: datetime | None, *, now: datetime,
                   config: FatigueConfig) -> bool:
    """§9: cooldown is temporary; `None` means never selected, hence not cooling down."""
    if last_selected_at is None:
        return False
    return now - last_selected_at < timedelta(hours=config.cooldown_hours)

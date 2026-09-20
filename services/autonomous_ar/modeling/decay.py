# services/autonomous_ar/modeling/decay.py
"""Recency weighting and regime detection (§27/§28): old evidence decays, it is not deleted.

Every function takes its `now`; nothing reads a clock. `None` in, `None` out for the
shift detector — an unknown baseline is not a shift (§28, P0 §2 "unknown ≠ zero").
Half-lives and thresholds arrive from the caller's `NonstationarityConfig` (persisted with
`policy_version`), so this module has no tunable constants of its own; changing a call's
half-life or threshold is a policy change, and old observations keep a positive weight
however far in the past they sit — discounting never becomes deletion.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

__all__ = [
    "detect_regime_shift", "effective_observation_count", "recency_weight", "weighted_reward",
]


def recency_weight(observed_at: datetime, *, now: datetime, half_life_days: float) -> float:
    """`0.5^(age/half-life)`, clamped to (0, 1]; future instants are clock skew."""
    if half_life_days <= 0.0:
        raise ValueError("half_life_days must be > 0")
    age_days = (now - observed_at).total_seconds() / 86400.0
    if age_days <= 0.0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def effective_observation_count(observation_instants: Sequence[datetime], *, now: datetime,
                                half_life_days: float) -> float:
    """§27: both raw history and the weighted current view exist; this is the weighted one."""
    return sum(recency_weight(instant, now=now, half_life_days=half_life_days)
               for instant in observation_instants)


def weighted_reward(rewards: Sequence[float], weights: Sequence[float]) -> float:
    """Recency-weighted mean reward; refuses degenerate inputs rather than guessing."""
    if len(rewards) != len(weights):
        raise ValueError("rewards and weights must be the same length")
    if not rewards:
        raise ValueError("weighted_reward needs at least one observation")
    total = sum(weights)
    if total <= 0.0:
        raise ValueError("weighted_reward refuses a zero total weight")
    return sum(reward * weight for reward, weight in zip(rewards, weights, strict=True)) / total


def detect_regime_shift(recent: float | None, baseline: float | None, *,
                        relative_threshold: float) -> bool | None:
    """§28: a material move in *either* direction; `None` when either side is unknown."""
    if recent is None or baseline is None:
        return None
    if relative_threshold <= 0.0:
        raise ValueError("relative_threshold must be > 0")
    denominator = max(abs(baseline), 1e-9)
    return abs(recent - baseline) / denominator >= relative_threshold

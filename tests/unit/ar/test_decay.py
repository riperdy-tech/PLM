# tests/unit/ar/test_decay.py
"""§27/§28: old evidence is down-weighted, never deleted; every function takes its `now`
(the persisted cycle instant) and knows no clock."""
from datetime import UTC, datetime, timedelta

import pytest

from services.autonomous_ar.modeling.decay import (
    detect_regime_shift, effective_observation_count, recency_weight, weighted_reward,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_recency_weight_is_half_after_one_half_life_and_capped_for_the_future() -> None:
    assert recency_weight(NOW, now=NOW, half_life_days=45.0) == 1.0
    assert recency_weight(NOW - timedelta(days=45), now=NOW, half_life_days=45.0) == 0.5
    assert recency_weight(NOW + timedelta(hours=1), now=NOW, half_life_days=45.0) == 1.0


def test_effective_observation_count_and_weighted_reward() -> None:
    instants = [NOW, NOW - timedelta(days=45)]
    effective = effective_observation_count(instants, now=NOW, half_life_days=45.0)
    assert effective == pytest.approx(1.5)
    assert weighted_reward([1.0, 0.0], [1.0, 1.0]) == pytest.approx(0.5)
    with pytest.raises(ValueError, match="at least one"):
        weighted_reward([], [])
    with pytest.raises(ValueError, match="zero total weight"):
        weighted_reward([0.5], [0.0])


def test_regime_shift_needs_both_sides_and_fires_in_either_direction() -> None:
    assert detect_regime_shift(None, 0.5, relative_threshold=0.3) is None
    assert detect_regime_shift(0.5, None, relative_threshold=0.3) is None
    assert detect_regime_shift(0.30, 0.60, relative_threshold=0.3) is True  # degraded
    assert detect_regime_shift(0.90, 0.60, relative_threshold=0.3) is True  # improved
    assert detect_regime_shift(0.55, 0.60, relative_threshold=0.3) is False


def test_history_is_discounted_never_deleted() -> None:
    ancient = NOW - timedelta(days=45 * 20)  # twenty half-lives old
    weight = recency_weight(ancient, now=NOW, half_life_days=45.0)
    assert weight == 0.5 ** 20
    assert weight > 0.0  # ~1e-6, small but present: nothing is hard-deleted
    with_history = effective_observation_count([NOW, ancient], now=NOW, half_life_days=45.0)
    fresh_only = effective_observation_count([NOW], now=NOW, half_life_days=45.0)
    assert with_history > fresh_only
    mixed = weighted_reward([1.0, 0.0], [1.0, weight])
    assert 0.0 < mixed < 1.0  # the old observation still pulls the mean down


def test_recompute_is_identical_for_the_same_stored_inputs() -> None:
    instants = [NOW, NOW - timedelta(days=10), NOW - timedelta(days=120)]
    weights = [recency_weight(instant, now=NOW, half_life_days=45.0) for instant in instants]
    first = (effective_observation_count(instants, now=NOW, half_life_days=45.0),
             weighted_reward([1.0, 0.4, 0.9], weights),
             detect_regime_shift(0.4, 0.6, relative_threshold=0.3))
    second = (effective_observation_count(instants, now=NOW, half_life_days=45.0),
              weighted_reward([1.0, 0.4, 0.9], weights),
              detect_regime_shift(0.4, 0.6, relative_threshold=0.3))
    assert first == second

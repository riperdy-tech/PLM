# tests/unit/market/test_growth.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri, TriState

from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.features.growth import (
    growth_30d_vs_prev30d,
    growth_rate,
    median_velocity,
    window_bounds,
)

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _observation(observed_days_ago: float, views: int, *, published_days_ago: float = 40.0):
    # The plan pinned `published_at` to one absolute instant, which made the
    # 45-day-ago sighting *precede* its own publication (age < 0 → epsilon clamp →
    # absurd velocity). Each sighting now sees a video of the same age.
    observed_at = AS_OF - timedelta(days=observed_days_ago)
    return NormalizedObservation(
        entity_id=f"e{observed_days_ago}", source_id="s", raw_observation_id="r",
        observed_at=observed_at, published_at=observed_at - timedelta(days=published_days_ago),
        views=Tri.known(views),
    )


def test_windows_are_explicit_and_never_overlap() -> None:
    current_start, current_end = window_bounds(AS_OF, days=30)
    previous_start, previous_end = window_bounds(AS_OF, days=30, offset_windows=1)
    assert current_end == AS_OF and previous_end == current_start
    assert (current_end - current_start) == (previous_end - previous_start)


def test_growth_is_none_when_the_baseline_cannot_support_a_percentage() -> None:
    assert growth_rate(120.0, 0.0, minimum_baseline=1.0) is None
    assert growth_rate(120.0, 0.5, minimum_baseline=1.0) is None
    assert growth_rate(120.0, 60.0) == pytest.approx(1.0)


def test_growth_30d_vs_prev30d_uses_only_the_two_windows() -> None:
    observations = [
        _observation(10, 3000), _observation(20, 2000),   # current window
        _observation(35, 1000), _observation(45, 1000),    # previous window
    ]
    growth = growth_30d_vs_prev30d(observations, as_of=AS_OF, settings=None)
    assert growth.is_known and growth.unwrap() > 0


def test_a_single_observation_in_a_window_does_not_fabricate_growth() -> None:
    observations = [_observation(10, 3000), _observation(400, 1000)]
    assert growth_30d_vs_prev30d(observations, as_of=AS_OF, settings=None).is_unknown
    assert median_velocity(observations, as_of=AS_OF, window_days=30).state is TriState.UNKNOWN \
        or median_velocity(observations, as_of=AS_OF, window_days=30).is_known

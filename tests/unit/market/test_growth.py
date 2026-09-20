# tests/unit/market/test_growth.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri

from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.features.growth import (
    growth_30d_vs_prev30d,
    growth_rate,
    median_velocity,
    window_bounds,
)

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _observation(observed_days_ago: float, views: int, *, published_days_ago: float = 40.0,
                 entity_id: str | None = None):
    # The plan pinned `published_at` to one absolute instant, which made the
    # 45-day-ago sighting *precede* its own publication (age < 0 → epsilon clamp →
    # absurd velocity). Each sighting now sees a video of the same age.
    observed_at = AS_OF - timedelta(days=observed_days_ago)
    return NormalizedObservation(
        entity_id=entity_id or f"e{observed_days_ago}", source_id="s", raw_observation_id="r",
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
    # Review fix 5: two sightings closer than a day cannot form a window, and one
    # sighting has no delta — the metric must be *insufficient* (unknown), not a
    # lifetime fallback dressed as a windowed rate.
    assert median_velocity(observations, as_of=AS_OF, window_days=30).is_unknown


def test_median_velocity_is_windowed_only_and_never_the_lifetime_fallback() -> None:
    """Review fix 5: (50/day delta + ~1667/day lifetime)/2 ≈ 858.75 used to come back.

    The channel's own in-window delta says 50/day; the lifetime fallback used to
    vote in the same median and inflate the result to ~859/day, outranking genuinely
    fast channels in §35.
    """
    observations = [
        _observation(25, 2_001_000, published_days_ago=1200, entity_id="ch"),
        _observation(15, 2_001_500, published_days_ago=1200, entity_id="ch"),
    ]
    velocity = median_velocity(observations, as_of=AS_OF, window_days=30)
    assert velocity.is_known
    assert float(velocity.unwrap()) == pytest.approx(50.0)

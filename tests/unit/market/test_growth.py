# tests/unit/market/test_growth.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri

from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.features.growth import (
    CURRENT_D0_30,
    HISTORICAL_D90_180,
    PRIOR_D30_60,
    PRIOR_D30_90,
    DayWindow,
    growth_between,
    growth_d0_30_vs_d30_60,
    growth_d0_30_vs_d30_90,
    growth_d0_30_vs_d90_180,
    growth_rate,
    median_recent_velocity,
    median_velocity,
    window_bounds,
    window_median_velocity,
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


def _series(entity_id: str, points: list[tuple[float, int]]) -> list[NormalizedObservation]:
    """One video sighted at (days_ago, cumulative views) points."""
    return [_observation(days_ago, views, entity_id=entity_id) for days_ago, views in points]


def test_windows_are_explicit_and_never_overlap() -> None:
    current_start, current_end = window_bounds(AS_OF, days=30)
    previous_start, previous_end = window_bounds(AS_OF, days=30, offset_windows=1)
    assert current_end == AS_OF and previous_end == current_start
    assert (current_end - current_start) == (previous_end - previous_start)


def test_window_names_state_their_span_in_days() -> None:
    assert [w.name for w in (CURRENT_D0_30, PRIOR_D30_60, PRIOR_D30_90, HISTORICAL_D90_180)] \
        == ["d0_30", "d30_60", "d30_90", "d90_180"]
    start, end = HISTORICAL_D90_180.bounds(AS_OF)
    assert end - start == timedelta(days=90)
    assert start == AS_OF - timedelta(days=180)
    with pytest.raises(ValueError):
        DayWindow(30, 30)


def test_a_100_day_old_sighting_lands_in_d90_180_not_in_the_previous_quarter() -> None:
    """R2: the old "prev90d" window was days 90–180 under a name that read as days 30–120."""
    moment = AS_OF - timedelta(days=100)
    assert HISTORICAL_D90_180.contains(moment, as_of=AS_OF)
    assert not PRIOR_D30_90.contains(moment, as_of=AS_OF)
    assert not CURRENT_D0_30.contains(moment, as_of=AS_OF)


def test_growth_against_the_historical_window_uses_only_days_90_to_180() -> None:
    observations = [
        *_series("v", [(20, 10_000), (10, 12_000)]),       # current: 200/day
        *_series("v", [(120, 1_000), (100, 2_000)]),       # d90_180: 50/day
    ]
    historical = growth_d0_30_vs_d90_180(observations, as_of=AS_OF)
    assert historical.is_known
    assert float(historical.unwrap()) == pytest.approx(3.0)  # (200 - 50) / 50
    # nothing was sighted in days 30–90 or 30–60: an empty window is unknown, not zero
    assert window_median_velocity(observations, as_of=AS_OF, window=PRIOR_D30_90).is_unknown
    assert growth_d0_30_vs_d30_90(observations, as_of=AS_OF).is_unknown
    assert growth_d0_30_vs_d30_60(observations, as_of=AS_OF).is_unknown


def test_growth_is_computed_from_windowed_deltas_not_lifetime_velocity() -> None:
    """A video whose lifetime average is high but whose recent deltas are slow is slowing.

    Lifetime views/day of each sighting (views / age) would call this flat or growing;
    the in-window deltas say 100/day now against 300/day in days 30–60.
    """
    observations = _series("v", [(55, 1_000_000), (45, 1_003_000),   # d30_60: 300/day
                                 (25, 1_006_000), (15, 1_007_000)])  # d0_30: 100/day
    growth = growth_d0_30_vs_d30_60(observations, as_of=AS_OF)
    assert growth.is_known
    assert float(growth.unwrap()) == pytest.approx((100.0 - 300.0) / 300.0)
    assert float(growth_d0_30_vs_d30_90(observations, as_of=AS_OF).unwrap()) == \
        pytest.approx((100.0 - 300.0) / 300.0)


def test_growth_refuses_overlapping_windows() -> None:
    with pytest.raises(ValueError, match="overlap"):
        growth_between([], as_of=AS_OF, current=PRIOR_D30_90, baseline=PRIOR_D30_60)


def test_growth_is_none_when_the_baseline_cannot_support_a_percentage() -> None:
    assert growth_rate(120.0, 0.0, minimum_baseline=1.0) is None
    assert growth_rate(120.0, 0.5, minimum_baseline=1.0) is None
    assert growth_rate(120.0, 60.0) == pytest.approx(1.0)


def test_growth_d0_30_vs_d30_60_uses_only_the_two_windows() -> None:
    observations = [
        *_series("a", [(20, 3_000), (10, 6_000)]),     # current window: 300/day
        *_series("a", [(45, 1_000), (35, 2_000)]),     # previous window: 100/day
        *_series("a", [(150, 0), (120, 9_000_000)]),   # far outside both: must not count
    ]
    growth = growth_d0_30_vs_d30_60(observations, as_of=AS_OF)
    assert growth.is_known and float(growth.unwrap()) == pytest.approx(2.0)


def test_a_single_observation_in_a_window_does_not_fabricate_growth() -> None:
    observations = [_observation(10, 3000), _observation(400, 1000)]
    assert growth_d0_30_vs_d30_60(observations, as_of=AS_OF).is_unknown
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


def _hourly(entity_id: str, points: list[tuple[float, int]]) -> list[NormalizedObservation]:
    return [
        NormalizedObservation(
            entity_id=entity_id, source_id="s", raw_observation_id=f"{entity_id}-{hours}",
            observed_at=AS_OF - timedelta(hours=hours), views=Tri.known(views),
        )
        for hours, views in points
    ]


def test_arm_72h_velocity_is_unknown_with_one_sighting() -> None:
    observations = _hourly("v", [(10, 5_000)])
    assert median_recent_velocity(observations, now=AS_OF, window_hours=72,
                                  min_span_fraction=0.5).is_unknown


def test_arm_24h_and_72h_velocity_are_medians_of_per_video_rates() -> None:
    observations = [
        *_hourly("fast", [(70, 0), (46, 2_000), (22, 4_000), (0, 6_000)]),
        *_hourly("slow", [(70, 0), (46, 100), (22, 200), (0, 300)]),
        *_hourly("new", [(22, 0), (0, 900)]),                               # 24h only
    ]
    v72 = median_recent_velocity(observations, now=AS_OF, window_hours=72,
                                 min_span_fraction=0.5)
    v24 = median_recent_velocity(observations, now=AS_OF, window_hours=24,
                                 min_span_fraction=0.5)
    # 72h: "new" spans 22h of 72h (< half) and does not vote; fast and slow span 70h
    assert float(v72.unwrap()) == pytest.approx((6_000 + 300) / 2 / (70 / 24))
    # 24h: each video's 22h span; new = 900 views over 22h
    assert float(v24.unwrap()) == pytest.approx(900 / (22 / 24))

def test_arm_features_expose_24h_and_72h_velocity() -> None:
    from services.market_intelligence.config import load_market_settings
    from services.market_intelligence.features.arm_features import compute_arm_features

    observations = _hourly("fast", [(70, 0), (46, 2_000), (22, 4_000), (0, 6_000)])
    features = compute_arm_features({"arm": observations}, as_of=AS_OF,
                                    settings=load_market_settings(platform=None))["arm"]
    assert float(features.median_velocity_72h.unwrap()) == pytest.approx(6_000 / (70 / 24))
    assert float(features.median_velocity_24h.unwrap()) == pytest.approx(2_000 / (22 / 24))
    single = compute_arm_features({"arm": observations[-1:]}, as_of=AS_OF,
                                  settings=load_market_settings(platform=None))["arm"]
    assert single.median_velocity_72h.is_unknown
    assert single.median_velocity_24h.is_unknown

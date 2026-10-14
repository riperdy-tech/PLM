# tests/unit/market/test_velocity.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri, TriState

from services.market_intelligence.features.velocity import (
    channel_relative_views,
    comment_rate,
    lifetime_views_per_day,
    like_rate,
    recent_views_per_day,
    video_age_hours,
    windowed_views_per_day,
)

OBSERVED = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
PUBLISHED = OBSERVED - timedelta(hours=48)


def test_age_is_hours_and_never_below_the_epsilon() -> None:
    assert video_age_hours(observed_at=OBSERVED, published_at=PUBLISHED) == 48.0
    assert video_age_hours(observed_at=OBSERVED, published_at=OBSERVED) > 0.0
    with pytest.raises(ValueError, match="timezone-aware"):
        video_age_hours(observed_at=OBSERVED, published_at=datetime(2026, 9, 18, 6, 0))


def test_lifetime_velocity_uses_an_explicit_age_and_keeps_unknown_unknown() -> None:
    assert lifetime_views_per_day(
        Tri.known(4800), observed_at=OBSERVED, published_at=PUBLISHED
    ).unwrap() == pytest.approx(2400.0)
    assert lifetime_views_per_day(
        Tri.unknown(), observed_at=OBSERVED, published_at=PUBLISHED
    ).state is TriState.UNKNOWN


def test_windowed_velocity_needs_two_observation_points() -> None:
    delta = windowed_views_per_day(
        Tri.known(1000), Tri.known(3400),
        observed_at_before=OBSERVED - timedelta(days=2), observed_at_after=OBSERVED,
    )
    assert delta.unwrap() == pytest.approx(1200.0)
    assert windowed_views_per_day(
        Tri.known(1000), Tri.unknown(),
        observed_at_before=OBSERVED - timedelta(days=2), observed_at_after=OBSERVED,
    ).is_unknown


def test_rates_and_channel_ratio_treat_unknown_denominators_as_unknown() -> None:
    assert like_rate(Tri.known(84), Tri.known(1200)).unwrap() == pytest.approx(0.07)
    assert comment_rate(Tri.known(12), Tri.known(1200)).unwrap() == pytest.approx(0.01)
    assert like_rate(Tri.known(84), Tri.known(0)).is_unknown  # no views: no rate, no division
    assert channel_relative_views(Tri.known(600), Tri.unknown()).is_unknown
    assert channel_relative_views(Tri.known(600), Tri.known(0)).is_unknown  # §11.3 unstable
    assert channel_relative_views(Tri.known(600), Tri.known(1200)).unwrap() == pytest.approx(0.5)

# -- R2: 24h / 72h velocity over timestamped sightings, relative to an injected now --

def _sightings(*points: tuple[float, float | None]) -> list[tuple[datetime, Tri[float]]]:
    """(hours before OBSERVED, views or None for unknown)."""
    return [(OBSERVED - timedelta(hours=hours),
             Tri.unknown() if views is None else Tri.known(views)) for hours, views in points]


def test_72h_velocity_is_unknown_with_a_single_sighting() -> None:
    assert recent_views_per_day(_sightings((10, 5_000)), now=OBSERVED, window_hours=72,
                                min_span_fraction=0.5).is_unknown


def test_72h_velocity_is_the_in_window_delta_per_day() -> None:
    sightings = _sightings((96, 0), (60, 1_000), (12, 3_000))  # 96h is outside 72h
    velocity = recent_views_per_day(sightings, now=OBSERVED, window_hours=72,
                                    min_span_fraction=0.5)
    assert velocity.unwrap() == pytest.approx(2_000 / 2.0)  # 48h span = 2 days


def test_24h_velocity_ignores_sightings_after_now_and_unknown_views() -> None:
    sightings = _sightings((20, 100), (14, None), (2, 1_100), (-5, 9_999_999))
    velocity = recent_views_per_day(sightings, now=OBSERVED, window_hours=24,
                                    min_span_fraction=0.5)
    assert velocity.unwrap() == pytest.approx(1_000 / (18 / 24))


def test_sightings_too_close_together_cannot_stand_for_the_window() -> None:
    sightings = _sightings((3, 100), (1, 400))  # a 2h span is not a 72h rate
    assert recent_views_per_day(sightings, now=OBSERVED, window_hours=72,
                                min_span_fraction=0.5).is_unknown
    assert recent_views_per_day(sightings, now=OBSERVED, window_hours=72,
                                min_span_fraction=0.01).is_known


def test_a_provider_correction_is_unknown_not_negative_velocity() -> None:
    assert recent_views_per_day(_sightings((20, 500), (2, 400)), now=OBSERVED,
                                window_hours=24, min_span_fraction=0.5).is_unknown


def test_recent_velocity_needs_an_aware_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        recent_views_per_day([], now=datetime(2026, 9, 20), window_hours=24,
                             min_span_fraction=0.5)

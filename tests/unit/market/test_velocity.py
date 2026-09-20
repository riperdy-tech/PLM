# tests/unit/market/test_velocity.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri, TriState

from services.market_intelligence.features.velocity import (
    channel_relative_views,
    comment_rate,
    lifetime_views_per_day,
    like_rate,
    video_age_hours,
    windowed_views_per_day,
)

OBSERVED = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
PUBLISHED = OBSERVED - timedelta(hours=48)


def test_age_is_hours_and_never_below_the_epsilon() -> None:
    assert video_age_hours(observed_at=OBSERVED, published_at=PUBLISHED) == 48.0
    assert video_age_hours(observed_at=OBSERVED, published_at=OBSERVED) > 0.0
    with pytest.raises(ValueError, match="timezone-aware"):
        video_age_hours(observed_at=OBSERVED, published_at=datetime(2026, 9, 18, 6, 0))  # noqa: DTZ001 — deliberately naive


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

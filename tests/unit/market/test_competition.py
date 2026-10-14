# tests/unit/market/test_competition.py
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri

from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.features.arm_features import arm_competition
from services.market_intelligence.features.competition import (
    competition_metrics,
    herfindahl_index,
    percentile,
    top_n_share,
)


def test_percentile_interpolates_and_handles_small_samples() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert percentile([], 0.5) == 0.0
    assert percentile([7.0], 0.9) == 7.0


def test_hhi_is_bounded_and_rises_with_concentration() -> None:
    assert herfindahl_index([0.5, 0.5]) == pytest.approx(0.5)
    assert herfindahl_index([1.0]) == pytest.approx(1.0)
    assert herfindahl_index([]) == 0.0
    assert herfindahl_index([0.25] * 4) == pytest.approx(0.25)


def test_competition_metrics_carry_their_sample_size_and_concentration() -> None:
    metrics = competition_metrics(
        video_velocities=[120.0, 80.0, 40.0, 20.0],
        channel_sizes=[820_000.0, 24_000.0, 80_000.0],
        channel_activities=[900.0, 100.0, 200.0],
        uploads_last_window=7, window_days=30,
    )
    assert metrics.active_video_count == 4
    assert metrics.active_channel_count == Tri.known(3)
    assert metrics.median_video_velocity == pytest.approx(60.0)
    assert metrics.p90_video_velocity >= metrics.p75_video_velocity >= metrics.median_video_velocity
    assert metrics.median_channel_size.unwrap() == pytest.approx(80_000.0)
    assert metrics.top_10_channel_share.unwrap() == pytest.approx(1.0)
    assert metrics.concentration_raw.unwrap() == pytest.approx(
        herfindahl_index([0.75, 0.08333333, 0.16666667]), rel=1e-3
    )
    assert 0.0 <= metrics.concentration_normalized.unwrap() <= 1.0
    assert metrics.sample_size == 4
    assert metrics.new_upload_rate == pytest.approx(7 / 30)
    assert top_n_share([5.0, 3.0, 2.0, 1.0], 2) == pytest.approx(0.7272727, rel=1e-6)

# -- R2: concentration is keyed on channel, and unattributed activity is unknown --

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _video(video_id: str, channel_id: str | None, *, views: int = 30_000,
           published_days_ago: float = 30.0) -> NormalizedObservation:
    return NormalizedObservation(
        entity_id=video_id, source_id="s", raw_observation_id=f"r-{video_id}",
        observed_at=AS_OF, published_at=AS_OF - timedelta(days=published_days_ago),
        channel_id=channel_id, views=Tri.known(views),
    )


def test_two_videos_from_one_channel_are_fully_concentrated() -> None:
    competition = arm_competition([_video("v1", "UC-one"), _video("v2", "UC-one")],
                                  as_of=AS_OF)
    assert competition.active_video_count == 2
    assert competition.active_channel_count == Tri.known(1)
    assert competition.concentration_raw.unwrap() == pytest.approx(1.0)
    assert competition.concentration_normalized.unwrap() == pytest.approx(1.0)


def test_equal_activity_across_two_channels_is_not_concentrated() -> None:
    competition = arm_competition([_video("v1", "UC-one"), _video("v2", "UC-two")],
                                  as_of=AS_OF)
    assert competition.concentration_raw.unwrap() == pytest.approx(0.5)
    assert competition.concentration_normalized.unwrap() == pytest.approx(0.0)


def test_a_video_sighted_many_times_is_one_competitor_not_many() -> None:
    sightings = [
        NormalizedObservation(
            entity_id="v1", source_id="s", raw_observation_id=f"r{day}",
            observed_at=AS_OF - timedelta(days=day), published_at=AS_OF - timedelta(days=60),
            channel_id="UC-one", views=Tri.known(1_000 * (10 - day)),
        )
        for day in range(5)
    ]
    competition = arm_competition([*sightings, _video("v2", "UC-two", views=9_000,
                                                      published_days_ago=60)], as_of=AS_OF)
    assert competition.active_video_count == 2
    # newest sighting of v1: 10,000 views / 60 days vs v2: 9,000 / 60 days
    assert competition.concentration_raw.unwrap() == pytest.approx(
        herfindahl_index([10 / 19, 9 / 19])
    )


def test_concentration_without_channel_ids_is_unknown_not_computed_from_video_ids() -> None:
    competition = arm_competition([_video("v1", None), _video("v2", None)], as_of=AS_OF)
    assert competition.active_video_count == 2
    assert competition.concentration_raw.is_unknown
    assert competition.concentration_normalized.is_unknown
    assert competition.top_10_channel_share.is_unknown
    assert competition.active_channel_count.is_unknown


def test_partly_attributed_activity_is_unknown_concentration() -> None:
    competition = arm_competition([_video("v1", "UC-one"), _video("v2", None)], as_of=AS_OF)
    assert competition.concentration_normalized.is_unknown


def test_a_channel_named_by_any_sighting_attributes_the_video() -> None:
    """Discovery sightings carry the channel; a later sighting without it keeps it."""
    older = replace(_video("v1", "UC-one"), observed_at=AS_OF - timedelta(days=2))
    newer = _video("v1", None)
    competition = arm_competition([older, newer, _video("v2", "UC-one")], as_of=AS_OF)
    assert competition.concentration_raw.unwrap() == pytest.approx(1.0)


def test_no_activity_has_no_concentration_rather_than_a_zero() -> None:
    metrics = competition_metrics(video_velocities=[], channel_sizes=[],
                                  channel_activities=[], uploads_last_window=0, window_days=30)
    assert metrics.concentration_raw.is_unknown
    assert metrics.active_channel_count == Tri.known(0)
    assert metrics.median_channel_size.is_unknown

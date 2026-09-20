# tests/unit/market/test_competition.py
import pytest

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
    assert metrics.active_channel_count == 3
    assert metrics.median_video_velocity == pytest.approx(60.0)
    assert metrics.p90_video_velocity >= metrics.p75_video_velocity >= metrics.median_video_velocity
    assert metrics.median_channel_size == pytest.approx(80_000.0)
    assert metrics.top_10_channel_share == pytest.approx(1.0)
    assert metrics.concentration_raw == pytest.approx(
        herfindahl_index([0.75, 0.08333333, 0.16666667]), rel=1e-3
    )
    assert 0.0 <= metrics.concentration_normalized <= 1.0
    assert metrics.sample_size == 4
    assert metrics.new_upload_rate == pytest.approx(7 / 30)
    assert top_n_share([5.0, 3.0, 2.0, 1.0], 2) == pytest.approx(0.7272727, rel=1e-6)

# tests/unit/market/test_normalization_scoring.py
import pytest

from services.market_intelligence.scoring.normalization import percentile_score, winsorize


def test_winsorization_clamps_the_tails_not_the_middle() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 1000.0]
    clamped = winsorize(values, lower_pct=0.1, upper_pct=0.9)
    assert min(clamped) == 1.0
    assert max(clamped) < 1000.0          # the outlier is pulled in
    assert sorted(clamped)[:3] == [1.0, 2.0, 3.0]
    assert 3.0 in clamped and 7.0 in clamped


def test_percentile_score_is_anchored_and_monotone() -> None:
    population = [float(n) for n in range(1, 101)]  # p10 = 10.1, p50 = 50.5, p90 = 90.9
    assert percentile_score(10.1, population=population) == pytest.approx(0.0, abs=0.5)
    assert percentile_score(50.5, population=population) == pytest.approx(50.0, abs=0.5)
    assert percentile_score(90.9, population=population) == pytest.approx(100.0, abs=0.5)
    scores = [percentile_score(value, population=population)
              for value in (1.0, 25.0, 50.0, 75.0, 120.0)]
    assert scores == sorted(scores)
    assert all(0.0 <= score <= 100.0 for score in scores)


def test_a_single_value_population_still_returns_a_bounded_score() -> None:
    assert percentile_score(7.0, population=[7.0]) == 50.0
    assert percentile_score(7.0, population=[]) == 50.0

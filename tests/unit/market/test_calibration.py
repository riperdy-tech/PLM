# tests/unit/market/test_calibration.py
import pytest

from services.market_intelligence.scoring.calibration import ScoreWeights, default_weights


def test_default_weights_match_the_spec_percentages_and_sum_to_one() -> None:
    weights = default_weights()
    assert weights.as_mapping() == {
        "demand": 0.25, "growth": 0.20, "competitor_velocity": 0.15, "competition": 0.15,
        "audience_quality": 0.10, "cross_platform": 0.10, "production_fit": 0.05,
    }
    assert weights.total == pytest.approx(1.0)


def test_weights_that_do_not_sum_to_one_are_refused() -> None:
    with pytest.raises(ValueError, match="sum to 1.0"):
        ScoreWeights(demand=0.9, growth=0.9, competitor_velocity=0.0, competition=0.0,
                     audience_quality=0.0, cross_platform=0.0, production_fit=0.0).validate()


def test_negative_weights_are_refused() -> None:
    with pytest.raises(ValueError):
        ScoreWeights(demand=-0.1, growth=0.3, competitor_velocity=0.2, competition=0.2,
                     audience_quality=0.2, cross_platform=0.2, production_fit=0.0).validate()

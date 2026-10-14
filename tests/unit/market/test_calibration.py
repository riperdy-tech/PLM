# tests/unit/market/test_calibration.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri

from services.market_intelligence.config import MarketSettings, load_market_settings
from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.scoring.calibration import ScoreWeights, default_weights
from services.market_intelligence.scoring.opportunity import (
    compute_component_scores,
    score_all_arms,
)

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


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

# -- R2: weights are MarketSettings values, validated on load, threaded to the scorer --

def test_settings_carry_the_spec_weights_by_default() -> None:
    assert load_market_settings(platform=None).score_weights == default_weights()


def test_settings_refuse_weights_that_do_not_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1.0"):
        load_market_settings(platform=None, score_weights={
            "demand": 0.5, "growth": 0.5, "competitor_velocity": 0.5, "competition": 0.0,
            "audience_quality": 0.0, "cross_platform": 0.0, "production_fit": 0.0,
        })
    with pytest.raises(ValueError, match="sum to 1.0"):
        MarketSettings(platform=None, score_weights=ScoreWeights(
            demand=0.3, growth=0.0, competitor_velocity=0.0, competition=0.0,
            audience_quality=0.0, cross_platform=0.0, production_fit=0.0))


def test_settings_refuse_negative_non_finite_missing_or_unknown_weights() -> None:
    base = default_weights().as_mapping()
    with pytest.raises(ValueError, match="negative"):
        load_market_settings(platform=None, score_weights={**base, "demand": -0.25,
                                                           "growth": 0.70})
    with pytest.raises(ValueError, match="finite"):
        load_market_settings(platform=None, score_weights={**base, "demand": float("nan")})
    missing = dict(base)
    del missing["production_fit"]
    with pytest.raises(ValueError, match="missing"):
        load_market_settings(platform=None, score_weights=missing)
    with pytest.raises(ValueError, match="unknown"):
        load_market_settings(platform=None, score_weights={**base, "virality": 0.0})


def test_configured_weights_reach_the_scorer() -> None:
    """All weight on demand: the score *is* the demand component."""
    weights = {name: 0.0 for name in default_weights().as_mapping()}
    weights["demand"] = 1.0
    settings = load_market_settings(platform=None, score_weights=weights)
    observations_by_arm = {
        arm: tuple(
            NormalizedObservation(
                entity_id=f"{arm}-v", source_id="s", raw_observation_id=f"{arm}-{day}",
                observed_at=AS_OF - timedelta(days=day),
                published_at=AS_OF - timedelta(days=90),
                views=Tri.known(views * (40 - day)), subscriber_count=Tri.known(subscribers),
            )
            for day in (25, 15, 5)
        )
        for arm, views, subscribers in (("a", 1_000, 10_000), ("b", 3_000, 10_000),
                                        ("c", 2_000, 10_000))
    }
    features = compute_arm_features(observations_by_arm, as_of=AS_OF, settings=settings)
    components = compute_component_scores(features, settings=settings)
    scores = score_all_arms(features, as_of=AS_OF, settings=settings)
    for arm, score in scores.items():
        assert score.market_opportunity_score == pytest.approx(
            float(components[arm].demand.unwrap()))
        assert score.basis_weight == pytest.approx(1.0)
    default_scores = score_all_arms(features, as_of=AS_OF,
                                    settings=load_market_settings(platform=None))
    assert any(default_scores[arm].market_opportunity_score
               != pytest.approx(scores[arm].market_opportunity_score) for arm in scores)

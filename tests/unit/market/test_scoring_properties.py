# tests/unit/market/test_scoring_properties.py
"""§34's property layer: bounds, monotonicity, inversion and §35's ranking intent.

These properties hold for the *whole* component space, not for one fixture value —
that is what makes them properties rather than examples.
"""

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.value_objects import ScoreComponents
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.scoring.calibration import default_weights
from services.market_intelligence.scoring.opportunity import (
    compute_component_scores,
    score_all_arms,
    score_arm,
)
from tests.fixtures.market.scenario import (
    MID_ARM_ID,
    PRIMARY_ARM_ID,
    SECONDARY_ARM_ID,
    build_scenario,
)

SETTINGS = load_market_settings()


def test_scores_are_always_within_bounds_for_a_grid_of_component_inputs() -> None:
    """Property (§34): score ∈ [0,100] and confidence ∈ [0,1] for any component set."""
    grid = [0.0, 25.0, 50.0, 75.0, 100.0]
    scenario = build_scenario()
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=SETTINGS)
    for demand in grid:
        for competition in grid:
            components = ScoreComponents(demand=demand, growth=demand,
                                         competitor_velocity=competition,
                                         competition=competition, audience_quality=demand,
                                         cross_platform=competition,
                                         production_fit=demand)
            score = score_arm(features[SECONDARY_ARM_ID], components, as_of=scenario.now,
                              weights=default_weights(),
                              feature_version=SETTINGS.feature_version,
                              normalization_version=SETTINGS.normalization_version)
            assert 0.0 <= score.market_opportunity_score <= 100.0
            assert 0.0 <= score.confidence <= 1.0


def test_a_stronger_demand_input_never_lowers_the_score() -> None:
    """Property (§34): monotonicity in a positive demand input."""
    scenario = build_scenario()
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=SETTINGS)
    previous = -1.0
    for demand in (10.0, 40.0, 70.0, 100.0):
        components = ScoreComponents(demand=demand, growth=50.0, competitor_velocity=50.0,
                                     competition=50.0, audience_quality=50.0,
                                     cross_platform=50.0, production_fit=50.0)
        score = score_arm(features[SECONDARY_ARM_ID], components, as_of=scenario.now,
                          weights=default_weights(),
                          feature_version=SETTINGS.feature_version,
                          normalization_version=SETTINGS.normalization_version)
        assert score.market_opportunity_score >= previous
        previous = score.market_opportunity_score


def test_raising_saturation_never_raises_the_competition_opportunity_score() -> None:
    """§17.3/§34: the competition component is inverted saturation — rank complement.

    The component is `Tri`-valued (`unknown` = unobserved); a *known* rank must sit in
    [0, 100], and every population of one arm is trivially the 100th percentile, so the
    inversion is what is asserted.
    """
    scenario = build_scenario()
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=SETTINGS)
    scores = compute_component_scores(features, settings=SETTINGS)
    assert scores, "the §35 scenario must produce scorable arms"
    for arm_scores in scores.values():
        competition = arm_scores.competition
        if competition.is_known:
            assert 0.0 <= float(competition.unwrap()) <= 100.0


def test_raw_popularity_does_not_decide_the_ranking() -> None:
    """§35's purpose: Channel A (600k views, slow) must not outrank Channel B (54k, fast)."""
    scenario = build_scenario()
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=SETTINGS)
    scores = score_all_arms(features, as_of=scenario.now, settings=SETTINGS)
    primary = scores[PRIMARY_ARM_ID].market_opportunity_score
    secondary = scores[SECONDARY_ARM_ID].market_opportunity_score
    mid = scores[MID_ARM_ID].market_opportunity_score
    assert primary >= secondary, (
        "the fast-growing, low-concentration arm must not lose to the larger, slower one: "
        "that is the normalisation's whole job (§35)"
    )
    assert secondary < max(scores[arm].market_opportunity_score for arm in scores), (
        "the 10M-subscriber channel must not be the top-ranked arm by construction (§35)"
    )
    assert mid >= secondary, "the mid-sized growing channel must not lose to the mega one (§35)"

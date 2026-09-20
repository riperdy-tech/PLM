# tests/unit/ar/test_reward_model.py
import random
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from plm_contracts import ObservationWindow, Tri

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.enums import ObservationMaturity
from services.autonomous_ar.domain.posterior import PosteriorState, PriorParameters
from services.autonomous_ar.domain.reward import RewardObservation
from services.autonomous_ar.modeling.reward_model import (
    NormalNormalRewardModel,
    UnmeasuredRewardError,
)

NOW = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}

MODEL = NormalNormalRewardModel()


def _arm() -> ContentArm:
    return ContentArm(arm_id="a", dimensions=ArmDimensions(genre="rnb"), p1_score=60.0,
                      p1_confidence=0.6, component_scores=COMPONENTS, observation_count=3,
                      source_count=1, as_of=NOW, score_version="p1-v1", freshness_seconds=0.0)


def _observation(reward: float | None) -> RewardObservation:
    return RewardObservation(
        experiment_id="exp-1", arm_id="a", content_id="c",
        observation_window=ObservationWindow.D7, maturity=ObservationMaturity.MATURE,
        observed_at=NOW, raw_metrics={},
        normalized_reward=Tri.unknown() if reward is None else Tri.known(reward),
        reward_version="p9-reward-v1", attribution_confidence=0.99,
    )


def _posterior() -> PosteriorState:
    prior = PriorParameters(mean=0.6, variance=0.05, confidence=0.6,
                            prior_version="p2-prior-v1")
    return MODEL.initialize_prior(_arm(), prior)


def test_initial_prior_is_the_state_itself_at_version_zero() -> None:
    state = _posterior()
    assert state.posterior_mean == 0.6 and state.posterior_variance == 0.05
    assert state.update_version == 0 and state.observation_count == 0
    assert state.model_version == "p2-nn-v1"


def test_updates_move_the_mean_in_the_reward_direction_and_shrink_variance() -> None:
    state = _posterior()
    up = MODEL.update(state, _observation(0.9), effective_weight=1.0, received_at=NOW)
    assert up.posterior_mean > state.posterior_mean
    assert up.posterior_variance < state.posterior_variance
    down = MODEL.update(state, _observation(0.1), effective_weight=1.0, received_at=NOW)
    assert down.posterior_mean < state.posterior_mean
    twice = MODEL.update(up, _observation(0.9), effective_weight=1.0, received_at=NOW)
    assert twice.observation_count == 2 and twice.posterior_variance < up.posterior_variance
    assert twice.update_version == up.update_version + 1


def test_unknown_reward_is_refused_never_treated_as_zero() -> None:
    with pytest.raises(UnmeasuredRewardError, match="normalized_reward"):
        MODEL.update(_posterior(), _observation(None), effective_weight=1.0, received_at=NOW)


def test_reward_outside_the_allowed_range_is_refused() -> None:
    with pytest.raises(ValueError, match="range"):
        MODEL.update(_posterior(), _observation(1.4), effective_weight=1.0, received_at=NOW)


def test_zero_variance_posterior_does_not_divide_by_zero() -> None:
    flattened = replace(_posterior(), posterior_variance=0.0)
    result = MODEL.update(flattened, _observation(0.7), effective_weight=1.0, received_at=NOW)
    assert result.posterior_variance >= MODEL.variance_floor


def test_sampling_is_seeded_reproducible_and_mutation_free() -> None:
    state = _posterior()
    before = state.posterior_mean, state.posterior_variance, state.update_version
    draw_1 = MODEL.sample(state, random.Random(7))
    draw_2 = MODEL.sample(state, random.Random(7))
    assert draw_1 == draw_2
    assert (state.posterior_mean, state.posterior_variance, state.update_version) == before
    # higher posterior mean ⇒ more wins over many seeded trials (§95 sampling expectations)
    stronger = replace(state, posterior_mean=0.8)
    wins = sum(
        1 for _ in range(200)
        if MODEL.sample(stronger, random.Random(0)) > MODEL.sample(state, random.Random(0))
    )
    assert wins > 150

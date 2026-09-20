# tests/unit/ar/test_exploration.py
import random
from datetime import UTC, datetime

import pytest
from plm_contracts import AllocationType

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.decision import ArmSample
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PosteriorState
from services.autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from services.autonomous_ar.policy.exploration import (
    effective_fractions,
    evidence_class,
    quota_from_fractions,
    reserve_baseline,
    sample_rewards,
    sampled_ranking,
)


def test_default_quota_apportions_exactly_seven_two_one() -> None:
    quota = quota_from_fractions(10, (0.70, 0.20, 0.10))
    assert quota == {AllocationType.EXPLOIT: 7, AllocationType.ADJACENT_EXPLORE: 2,
                     AllocationType.RADICAL_EXPLORE: 1}
    small = quota_from_fractions(2, (0.70, 0.20, 0.10))
    assert sum(small.values()) == 2 and small[AllocationType.EXPLOIT] >= 1


def test_baseline_reservation_is_inside_the_exploit_slice() -> None:
    assert reserve_baseline(10, DEFAULT_POLICY) == 1
    quota = quota_from_fractions(10, (0.70, 0.20, 0.10))
    assert reserve_baseline(10, DEFAULT_POLICY) <= quota[AllocationType.EXPLOIT]


def test_low_confidence_and_regime_shift_raise_exploration_without_breaking_sum() -> None:
    base = (DEFAULT_POLICY.exploit_fraction, DEFAULT_POLICY.adjacent_explore_fraction,
            DEFAULT_POLICY.radical_explore_fraction)
    low = effective_fractions(DEFAULT_POLICY, low_confidence=True, regime_shift=False)
    shift = effective_fractions(DEFAULT_POLICY, low_confidence=False, regime_shift=True)
    calm = effective_fractions(DEFAULT_POLICY, low_confidence=False, regime_shift=False)
    assert calm == base
    assert sum(low) == pytest.approx(1.0) and sum(shift) == pytest.approx(1.0)
    assert low[0] < base[0] and low[2] > base[2]
    assert shift[0] < base[0] and shift[1] > base[1] and shift[2] > base[2]


def test_evidence_class_prefers_measured_then_adjacent_then_radical() -> None:
    measured, adjacent = frozenset({"m"}), frozenset({"n"})
    assert evidence_class("m", measured_arm_ids=measured, adjacent_arm_ids=adjacent) \
        is AllocationType.EXPLOIT
    assert evidence_class("n", measured_arm_ids=measured, adjacent_arm_ids=adjacent) \
        is AllocationType.ADJACENT_EXPLORE
    assert evidence_class("x", measured_arm_ids=measured, adjacent_arm_ids=adjacent) \
        is AllocationType.RADICAL_EXPLORE


NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _arm(arm_id: str) -> ContentArm:
    return ContentArm(arm_id=arm_id, dimensions=ArmDimensions(genre="rnb"), p1_score=60.0,
                      p1_confidence=0.6, component_scores=dict(COMPONENTS),
                      observation_count=1, source_count=1, as_of=NOW, score_version="p1-v1",
                      freshness_seconds=0.0)


def _posterior(arm_id: str, mean: float) -> PosteriorState:
    return PosteriorState(arm_id=arm_id, model_type="normal-normal", model_version="p2-nn-v1",
                          prior_mean=mean, prior_variance=0.01, posterior_mean=mean,
                          posterior_variance=0.001, observation_count=5,
                          effective_observation_count=5.0, update_version=1,
                          last_update_at=NOW)


def test_sampling_is_seeded_and_ranking_is_a_total_order() -> None:
    model = NormalNormalRewardModel()
    arms = [_arm("a"), _arm("b")]
    posteriors = {"a": _posterior("a", 0.7), "b": _posterior("b", 0.3)}
    first = sample_rewards(arms, posteriors, model=model, rng=random.Random(11))
    second = sample_rewards(arms, posteriors, model=model, rng=random.Random(11))
    assert first == second
    ranked = sampled_ranking([ArmSample(arm_id="b", sampled_reward=0.5,
                                        posterior_expected_reward=0.4),
                              ArmSample(arm_id="a", sampled_reward=0.5,
                                        posterior_expected_reward=0.6)])
    assert [sample.arm_id for sample in ranked] == ["a", "b"], "ties break on arm_id"

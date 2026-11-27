"""Rewards on the channel's own scale (review 2026-11-26, #39).

P9's rewards cluster around 0.5-0.75, the fixed observation variance (0.09) swamped that
spread, P1 priors spanned 0.05-0.95 and the absolute 0.25 retirement never fired, so
learning barely moved. Now: once the channel has a few measured videos, each reward is
its rank among them; the observation variance comes from the observed ranks; priors sit
on the channel baseline with only a small P1 tilt; and retirement (0.25) means "in the
channel's bottom quarter", a relative threshold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.decisioning.cycle import (
    CycleRequest,
    MarketSnapshot,
    run_allocation_cycle,
)
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PriorParameters
from services.autonomous_ar.ingestion.result_adapter import store_result
from services.autonomous_ar.modeling.evidence import (
    CHANNEL_BASELINE,
    MIN_RANKED_VIDEOS,
    channel_ranks,
    channel_reward_scale,
    pooled_observation_variance,
    posterior_as_of,
)
from services.autonomous_ar.modeling.prior import PRIOR_TILT, cold_start_prior
from services.autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from services.autonomous_ar.modeling.update import apply_pending_updates
from services.autonomous_ar.policy.lifecycle import evaluate_retirements
from services.autonomous_ar.storage import repositories
from tests.support.ar_db import ar_session, create_ar_engine
from tests.support.seams import unmeasured_raw_metrics

NOW = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
SETTINGS = load_ar_settings()
BRAND = "longform_signal"
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _arm(arm_id: str, *, score: float = 60.0, confidence: float = 0.6) -> ContentArm:
    return ContentArm(arm_id=arm_id, dimensions=ArmDimensions(genre=f"g-{arm_id}"),
                      p1_score=score, p1_confidence=confidence,
                      component_scores=dict(COMPONENTS), observation_count=3, source_count=1,
                      as_of=NOW, score_version="p1-v1", freshness_seconds=0.0)


def test_ranks_are_mid_ranks_and_ties_share_them() -> None:
    assert channel_ranks([("a", 0.6)]) == {"a": 0.5}
    assert channel_ranks([("a", 0.6), ("b", 0.7), ("c", 0.65), ("d", 0.65)]) == {
        "a": 0.125, "c": 0.5, "d": 0.5, "b": 0.875}


def test_observation_variance_comes_from_repeat_videos_of_an_arm() -> None:
    by_arm = {"a": [0.1, 0.5, 0.3], "b": [0.8, 0.9]}
    # pooled within-arm sample variance: (0.08 + 0.005) / (2 + 1)
    assert pooled_observation_variance(by_arm, default=0.09) == pytest.approx(0.085 / 3)
    assert pooled_observation_variance({"a": [0.1, 0.3], "b": [0.5]}, default=0.09) == 0.09, (
        "too few repeats: the configured default")
    assert pooled_observation_variance({"a": [0.5, 0.5, 0.5, 0.5]}, default=0.09) == 0.01, (
        "floored: identical rewards never make the model certain")


def test_priors_sit_on_the_channel_baseline_with_a_small_p1_tilt() -> None:
    high = cold_start_prior(_arm("a", score=100.0, confidence=1.0), settings=SETTINGS,
                            policy=DEFAULT_POLICY)
    low = cold_start_prior(_arm("a", score=0.0, confidence=1.0), settings=SETTINGS,
                           policy=DEFAULT_POLICY)
    assert high.mean == pytest.approx(CHANNEL_BASELINE + PRIOR_TILT / 2)
    assert low.mean == pytest.approx(CHANNEL_BASELINE - PRIOR_TILT / 2)
    assert high.prior_version == SETTINGS.prior_version == "p2-prior-v2"


def _seed(session, arm_id: str) -> None:  # type: ignore[no-untyped-def]
    repositories.upsert_arm_state(session, brand_id=BRAND, arm_id=arm_id, now=NOW)
    repositories.upsert_posterior(
        session, NormalNormalRewardModel().initialize_prior(
            _arm(arm_id), PriorParameters(mean=0.5, variance=0.04, confidence=0.6,
                                          prior_version="p2-prior-v2")),
        brand_id=BRAND, expected_version=0)


def _result(session, arm_id: str, index: int, reward: float) -> None:  # type: ignore[no-untyped-def]
    store_result(session, {
        "schema_version": "1.0", "experiment_id": f"exp-{arm_id}-{index}", "arm_id": arm_id,
        "content_id": f"video-{arm_id}-{index}", "observation_window": "28d",
        "status": "mature", "observed_at": (NOW - timedelta(hours=index)).isoformat(),
        "raw_metrics": unmeasured_raw_metrics(), "normalized_reward": reward,
        "reward_version": "p9-reward-v1", "attribution_confidence": 0.99,
    }, brand_id=BRAND, now=NOW)


def test_clustered_rewards_separate_arms_on_the_channels_own_scale() -> None:
    """Five videos per arm, rewards 0.60-0.69: on P9's raw scale the weak arm is far above
    0.25 and never retires; ranked against the channel it is the bottom half, its
    posterior falls below the baseline, and the strong arm rises above it."""
    engine = create_ar_engine()
    with ar_session(engine) as session:
        for arm_id in ("strong", "weak"):
            _seed(session, arm_id)
        for index in range(5):
            _result(session, "strong", index, 0.65 + index * 0.01)
            _result(session, "weak", index, 0.60 + index * 0.01)
        assert apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                     policy=DEFAULT_POLICY).applied == 10
        scale = channel_reward_scale(session, brand_id=BRAND, as_of=NOW,
                                     default_variance=SETTINGS.observation_variance)
        assert scale.ranked and len(scale.ranks) == 10
        assert scale.observation_variance < SETTINGS.observation_variance
        model = NormalNormalRewardModel(observation_variance=scale.observation_variance)
        posteriors = {arm_id: posterior_as_of(
            session, repositories.load_posterior(session, arm_id), model=model,
            policy=DEFAULT_POLICY, as_of=NOW, scale=scale)
            for arm_id in ("strong", "weak")}
        raw = {arm_id: posterior_as_of(
            session, repositories.load_posterior(session, arm_id),
            model=NormalNormalRewardModel(), policy=DEFAULT_POLICY, as_of=NOW)
            for arm_id in ("strong", "weak")}
        ranked_gap = posteriors["strong"].posterior_mean - posteriors["weak"].posterior_mean
        raw_gap = raw["strong"].posterior_mean - raw["weak"].posterior_mean
        assert ranked_gap > 5 * raw_gap
        assert posteriors["weak"].posterior_mean < CHANNEL_BASELINE \
            < posteriors["strong"].posterior_mean
        # the stored cache is on the same scale as the draw
        stored = repositories.load_posterior(session, "weak")
        assert stored is not None and stored.posterior_mean < CHANNEL_BASELINE
        assert evaluate_retirements(raw, mature_counts={"strong": 5, "weak": 5},
                                    policy=DEFAULT_POLICY, now=NOW) == ()
        assert [d.arm_id for d in evaluate_retirements(
            posteriors, mature_counts={"strong": 5, "weak": 5},
            policy=DEFAULT_POLICY.model_copy(update={"retirement": DEFAULT_POLICY.retirement
                                                     .model_copy(update={
                                                         "weak_posterior_threshold": 0.4})}),
            now=NOW)] == ["weak"]


def test_a_young_channel_keeps_p9s_raw_scale() -> None:
    """With fewer than MIN_RANKED_VIDEOS videos there is nothing to rank against: the
    first video would always be the median. P9's raw reward is used until then."""
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed(session, "a")
        for index in range(MIN_RANKED_VIDEOS - 1):
            _result(session, "a", index, 0.9)
        apply_pending_updates(session, settings=SETTINGS, now=NOW, policy=DEFAULT_POLICY)
        scale = channel_reward_scale(session, brand_id=BRAND, as_of=NOW,
                                     default_variance=SETTINGS.observation_variance)
        assert not scale.ranked and scale.observation_variance == SETTINGS.observation_variance
        stored = repositories.load_posterior(session, "a")
        assert stored is not None and stored.posterior_mean > 0.5


def test_a_cold_start_prior_follows_p1_until_the_arm_has_evidence() -> None:
    """Review 2026-11-26: the first cycle's prior was stored and never refreshed, so an
    unmeasured arm kept the market view of its first sighting forever."""
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id=BRAND, created_at=NOW)

        def cycle(at: datetime, score: float) -> float:
            run_allocation_cycle(
                session, CycleRequest(brand_id=BRAND, as_of=at, slot_count=1, budget=1.0,
                                      policy_version="p2-v1"),
                settings=SETTINGS, now=at,
                market=MarketSnapshot(arms=(_arm("a", score=score, confidence=0.9),),
                                      as_of=at, source="live", score_version="p1-v1"))
            stored = repositories.load_posterior(session, "a")
            assert stored is not None
            return stored.prior_mean

        first = cycle(NOW, 90.0)
        second = cycle(NOW + timedelta(days=7), 20.0)
        assert second < CHANNEL_BASELINE < first

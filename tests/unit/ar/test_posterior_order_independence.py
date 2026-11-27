"""The stored posteriors follow the rewards, whatever order the rewards arrive in.

CI Gate R5 (run 45990841649): the strong arm earned [0.9419, 0.943] and the weak arm
[0.7497, 0.7513], yet the stored posterior of the strong arm (0.6396) ended below the
weak one (0.6748). Each update rewrote only the updated arm's posterior, on the channel
scale of that moment: the weak arm's two videos were folded in on P9's raw scale (the
channel had fewer than three videos), the strong arm's later on the ranked scale, so the
two stored rows were on different scales. An update pass now recomputes every measured
arm of the brand from all its stored rewards on the one current channel scale.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import permutations

import pytest
from autonomous_ar.config import load_ar_settings
from autonomous_ar.domain.arm import ArmDimensions, ContentArm
from autonomous_ar.domain.policy import DEFAULT_POLICY
from autonomous_ar.domain.posterior import PriorParameters
from autonomous_ar.ingestion.result_adapter import store_result
from autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from autonomous_ar.modeling.update import apply_pending_updates
from autonomous_ar.storage import repositories

from tests.support.ar_db import ar_session, create_ar_engine
from tests.support.seams import unmeasured_raw_metrics

NOW = datetime(2026, 12, 1, tzinfo=UTC)
SETTINGS = load_ar_settings()
BRAND = "longform_signal"
PRIORS = {"strong": 0.4799, "weak": 0.5071}
REWARDS = (("strong", 0, 0.9419), ("strong", 1, 0.943), ("weak", 0, 0.7497),
           ("weak", 1, 0.7513))
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _seed(session) -> None:  # type: ignore[no-untyped-def]
    for arm_id, mean in PRIORS.items():
        repositories.upsert_arm_state(session, brand_id=BRAND, arm_id=arm_id, now=NOW)
        arm = ContentArm(arm_id=arm_id, dimensions=ArmDimensions(genre=f"g-{arm_id}"),
                         p1_score=50.0, p1_confidence=0.6, component_scores=dict(COMPONENTS),
                         observation_count=3, source_count=1, as_of=NOW,
                         score_version="p1-v2", freshness_seconds=0.0)
        repositories.upsert_posterior(session, NormalNormalRewardModel().initialize_prior(
            arm, PriorParameters(mean=mean, variance=0.0656, confidence=0.6,
                                 prior_version=SETTINGS.prior_version)),
            brand_id=BRAND, expected_version=0)


def _deliver(session, arm_id: str, index: int, reward: float,  # type: ignore[no-untyped-def]
             at: datetime) -> None:
    store_result(session, {
        "schema_version": "1.0", "experiment_id": f"exp-{arm_id}-{index}", "arm_id": arm_id,
        "content_id": f"video-{arm_id}-{index}", "observation_window": "28d",
        "status": "mature", "observed_at": at.isoformat(),
        "raw_metrics": unmeasured_raw_metrics(), "normalized_reward": reward,
        "reward_version": "p9-reward-v1", "attribution_confidence": 0.99,
    }, brand_id=BRAND, now=at)


def _stored(session) -> dict[str, float]:  # type: ignore[no-untyped-def]
    means = {}
    for arm_id in PRIORS:
        posterior = repositories.load_posterior(session, arm_id)
        assert posterior is not None
        means[arm_id] = posterior.posterior_mean
    return means


@pytest.mark.parametrize("order", list(permutations(range(len(REWARDS)))))
def test_every_arrival_order_keeps_the_stronger_arm_above(order: tuple[int, ...]) -> None:
    """One update pass per arriving reward (the runs' learn steps interleave)."""
    with ar_session(create_ar_engine()) as session:
        _seed(session)
        for step, index in enumerate(order):
            arm_id, video, reward = REWARDS[index]
            at = NOW + timedelta(hours=step)
            _deliver(session, arm_id, video, reward, at)
            apply_pending_updates(session, settings=SETTINGS, now=at, policy=DEFAULT_POLICY)
        means = _stored(session)
        assert means["strong"] > means["weak"], (order, means)
        assert means["strong"] > PRIORS["strong"], (order, means)
        for arm_id in PRIORS:
            posterior = repositories.load_posterior(session, arm_id)
            assert posterior is not None and posterior.observation_count == 2
            assert posterior.update_version == 2


def test_the_stored_posteriors_do_not_depend_on_the_arrival_order() -> None:
    """Same rewards, same final instant: the same stored posteriors in every order."""
    seen = set()
    for order in permutations(range(len(REWARDS))):
        with ar_session(create_ar_engine()) as session:
            _seed(session)
            for step, index in enumerate(order):
                arm_id, video, reward = REWARDS[index]
                _deliver(session, arm_id, video, reward, NOW)
                if step % 2:
                    apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                          policy=DEFAULT_POLICY)
            seen.add(tuple(round(v, 12) for v in _stored(session).values()))
    assert len(seen) == 1, seen

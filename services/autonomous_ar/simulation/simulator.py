# services/autonomous_ar/simulation/simulator.py
"""§68: the offline simulator over the *same* policy functions production runs.

Mechanics only (§70): it validates behaviour (does the policy learn, explore, respect caps,
adapt), never economics. Replay is exact — same spec + same config ⇒ identical trace.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from plm_contracts import AllocationType, ObservationWindow, Tri

from ..config import ARSettings, load_ar_settings
from ..domain.arm import ArmDimensions, ContentArm
from ..domain.enums import ArmLifecycleStatus, ObservationMaturity
from ..domain.policy import DEFAULT_POLICY, PolicyConfig
from ..domain.posterior import PosteriorState
from ..domain.reward import RewardObservation
from ..modeling.prior import cold_start_prior
from ..modeling.reward_model import NormalNormalRewardModel
from ..policy.constraints import apply_portfolio_constraints
from ..policy.exploration import (
    effective_fractions,
    evidence_class,
    quota_from_fractions,
    sample_rewards,
    sampled_ranking,
)
from .scenarios import ScenarioSpec, SimArm

__all__ = ["SimConfig", "SimulationTrace", "run_simulation"]

_COMPONENTS = {name: 50.0 for name in
               ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
                "cross_platform", "production_fit")}


@dataclass(frozen=True, slots=True)
class SimConfig:
    cycles: int
    slot_count: int
    seed: int
    policy: PolicyConfig = DEFAULT_POLICY


@dataclass(frozen=True, slots=True)
class SimulationTrace:
    selection_counts: Mapping[str, int]
    rewards: tuple[float, ...]
    selected_by_cycle: tuple[tuple[str, ...], ...]
    exploration_by_cycle: tuple[float, ...]
    posterior_means_final: Mapping[str, float]
    regret_proxy: float


def _content_arm(sim_arm: SimArm, *, score: float, as_of: datetime) -> ContentArm:
    return ContentArm(
        arm_id=sim_arm.arm_id,
        dimensions=ArmDimensions(genre=sim_arm.genre, use_cases=(sim_arm.use_case,),
                                 language=sim_arm.language),
        p1_score=score, p1_confidence=0.7, component_scores=dict(_COMPONENTS),
        observation_count=5, source_count=2, as_of=as_of, score_version="p1-v1",
        freshness_seconds=0.0,
    )


def _simulated_observation(spec: ScenarioSpec, arm_id: str,
                           true_means: Mapping[str, float], rng: random.Random,
                           as_of: datetime, settings: ARSettings) -> RewardObservation:
    variance = next(arm.true_variance for arm in spec.arms if arm.arm_id == arm_id)
    draw = min(1.0, max(0.0, rng.gauss(true_means[arm_id], variance ** 0.5)))
    return RewardObservation(
        experiment_id=f"sim-{arm_id}-{as_of.isoformat()}", arm_id=arm_id,
        content_id=f"content-{arm_id}", observation_window=ObservationWindow.D7,
        maturity=ObservationMaturity.MATURE, observed_at=as_of, raw_metrics={},
        normalized_reward=Tri.known(draw),
        reward_version=settings.simulation_reward_version, attribution_confidence=1.0,
    )


def run_simulation(spec: ScenarioSpec, config: SimConfig) -> SimulationTrace:
    """`cycles` allocation decisions, with simulated outcomes, entirely in memory."""
    settings = load_ar_settings(similarity_threshold=0.6)
    model = NormalNormalRewardModel()
    policy = config.policy
    start = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    rng = random.Random(config.seed)
    selection_counts: dict[str, int] = {arm.arm_id: 0 for arm in spec.arms}
    rewards: list[float] = []
    selected_by_cycle: list[tuple[str, ...]] = []
    exploration_by_cycle: list[float] = []
    posteriors: dict[str, PosteriorState] = {}
    true_means = {arm.arm_id: arm.true_mean for arm in spec.arms}

    for cycle_index in range(config.cycles):
        as_of = start + timedelta(days=7 * cycle_index)
        if spec.regime_change_at is not None and cycle_index >= spec.regime_change_at:
            true_means.update(spec.regime_means)
        scores = dict(spec.p1_scores)
        if spec.shock_at is not None and cycle_index >= spec.shock_at:
            scores.update(spec.shocked_scores)
        arms = tuple(_content_arm(arm, score=scores[arm.arm_id], as_of=as_of)
                     for arm in spec.arms)
        arm_map = {arm.arm_id: arm for arm in arms}
        for arm in arms:
            if arm.arm_id not in posteriors:
                prior = cold_start_prior(arm, settings=settings, policy=policy)
                posteriors[arm.arm_id] = model.initialize_prior(arm, prior)
        measured = frozenset(arm_id for arm_id, state in posteriors.items()
                             if state.observation_count > 0)
        classes = {arm.arm_id: evidence_class(arm.arm_id, measured_arm_ids=measured,
                                              adjacent_arm_ids=frozenset()) for arm in arms}
        low_confidence = not measured
        fractions = effective_fractions(policy, low_confidence=low_confidence,
                                        regime_shift=False)
        quota = quota_from_fractions(config.slot_count, fractions)
        ranked = sampled_ranking(sample_rewards(arms, posteriors, model=model, rng=rng))
        portfolio = apply_portfolio_constraints(
            ranked, classes=classes, arms=arm_map,
            states={arm.arm_id: ArmLifecycleStatus.ACTIVE for arm in arms},
            policy=policy, slot_count=config.slot_count, quota=quota, baselines=(),
            clusters={arm.arm_id: arm.arm_id for arm in arms},  # F's caps via genre caps
        )
        selected = [selection.arm_id for selection in portfolio.selections]
        for selection in portfolio.selections:
            selection_counts[selection.arm_id] += 1
            observation = _simulated_observation(spec, selection.arm_id, true_means,
                                                 rng, as_of, settings)
            posteriors[selection.arm_id] = model.update(
                posteriors[selection.arm_id], observation, effective_weight=1.0,
                received_at=as_of)
            rewards.append(observation.normalized_reward.unwrap())
        selected_by_cycle.append(tuple(selected))
        exploration = sum(1 for selection in portfolio.selections
                          if selection.allocation_type is not AllocationType.EXPLOIT)
        exploration_by_cycle.append(exploration / config.slot_count if config.slot_count else 0.0)

    best_mean = max(true_means.values())
    regret = best_mean * config.cycles * config.slot_count - sum(rewards)
    return SimulationTrace(
        selection_counts=selection_counts, rewards=tuple(rewards),
        selected_by_cycle=tuple(selected_by_cycle),
        exploration_by_cycle=tuple(exploration_by_cycle),
        posterior_means_final={arm_id: state.posterior_mean
                               for arm_id, state in posteriors.items()},
        regret_proxy=regret,
    )

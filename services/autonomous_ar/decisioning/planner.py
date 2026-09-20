# services/autonomous_ar/decisioning/planner.py
"""§29 steps 1–9 as pure functions: eligibility, evidence classes, clusters, samples.

No repository access and no clock: the cycle passes the snapshot, the posteriors and the
policy in and gets back a ranked, class-labelled candidate set. That is what makes the
leak-prone part of P2 ("which arms competed, with what data") unit-testable.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from plm_contracts import AllocationType

from ..config import ARSettings
from ..domain.arm import ContentArm
from ..domain.decision import ArmSample
from ..domain.enums import ReasonCode
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState
from ..modeling.fatigue import fatigue_penalty
from ..modeling.reward_model import RewardModel
from ..modeling.similarity import arm_similarity, cluster_arms
from ..policy.exploration import evidence_class, sample_rewards, sampled_ranking

__all__ = ["PreparedCycle", "cluster_snapshot", "prepare_cycle"]


@dataclass(frozen=True, slots=True)
class PreparedCycle:
    ranked: tuple[ArmSample, ...]
    classes: Mapping[str, AllocationType]
    clusters: Mapping[str, str]
    warnings: tuple[str, ...]
    ineligible: Mapping[str, str]  # arm_id → ReasonCode value, for the flagged decision row


def cluster_snapshot(arms: Sequence[ContentArm], *, threshold: float) -> dict[str, str]:
    """Live clustering over a snapshot; pairs below the threshold are simply absent."""
    pairs: list[tuple[str, str, float]] = []
    for index, arm in enumerate(arms):
        for other in arms[index + 1:]:
            score = arm_similarity(arm.dimensions, other.dimensions)
            if score >= threshold:
                pairs.append((arm.arm_id, other.arm_id, score))
    return cluster_arms(pairs, threshold=threshold)


def prepare_cycle(*, arms: Sequence[ContentArm], posteriors: Mapping[str, PosteriorState],
                  policy: PolicyConfig, settings: ARSettings, model: RewardModel,
                  rng: random.Random, selection_recency: Mapping[str, float],
                  min_components: int, warnings: Sequence[str]) -> PreparedCycle:
    """Eligibility (§83/§111), evidence classes (§14), fatigue-adjusted ranking (§26/§58)."""
    ineligible: dict[str, str] = {}
    ranked_arms: list[ContentArm] = []
    for arm in arms:
        if not arm.is_rankable(minimum=min_components):
            ineligible[arm.arm_id] = ReasonCode.EVIDENCE_INSUFFICIENT.value
            continue
        if arm.freshness_seconds > policy.max_market_data_age_hours * 3600.0:
            ineligible[arm.arm_id] = ReasonCode.MARKET_STALE.value
            continue
        if arm.arm_id not in posteriors:
            ineligible[arm.arm_id] = "NO_POSTERIOR"
            continue
        ranked_arms.append(arm)

    clusters = cluster_snapshot(ranked_arms, threshold=settings.similarity_threshold)
    measured = frozenset(arm.arm_id for arm in ranked_arms
                         if posteriors[arm.arm_id].observation_count > 0)
    adjacent: set[str] = set()
    for arm_id in measured:
        cluster_id = clusters.get(arm_id)
        adjacent.update(other_id for other_id, other_cluster in clusters.items()
                        if other_cluster == cluster_id and other_id != arm_id)
    classes = {arm.arm_id: evidence_class(arm.arm_id, measured_arm_ids=measured,
                                          adjacent_arm_ids=frozenset(adjacent - measured))
               for arm in ranked_arms}

    collected = list(warnings)
    adjusted: list[ArmSample] = []
    for sample in sample_rewards(ranked_arms, posteriors, model=model, rng=rng):
        penalty = fatigue_penalty(selection_recency.get(sample.arm_id, 0.0),
                                  config=policy.fatigue)
        if penalty > 0.0:
            collected.append(ReasonCode.FATIGUE_COOLDOWN.value)
        adjusted.append(ArmSample(
            arm_id=sample.arm_id,
            sampled_reward=max(0.0, sample.sampled_reward - penalty),
            posterior_expected_reward=sample.posterior_expected_reward,
        ))
    return PreparedCycle(ranked=sampled_ranking(adjusted), classes=classes, clusters=clusters,
                         warnings=tuple(dict.fromkeys(collected)), ineligible=ineligible)

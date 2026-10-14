# services/autonomous_ar/modeling/reward_model.py
"""Posterior mechanics: Normal–Normal conjugate, stdlib only, seeded and replayable (§11, §66).

The policy depends on the `RewardModel` protocol, never on this class (§66) — a future
contextual model replaces it without changing the public surface. Sampling consumes an
injected `random.Random`; nothing here reads a clock or the global RNG (§67).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from ..domain.arm import ContentArm
from ..domain.posterior import PosteriorState, PriorParameters
from ..domain.reward import RewardObservation

__all__ = ["NormalNormalRewardModel", "RewardModel", "UnmeasuredRewardError"]


class UnmeasuredRewardError(ValueError):
    """The observation carries no measured `normalized_reward` (R11: P9 owns the number)."""


class RewardModel(Protocol):
    """§66's interface, names verbatim."""

    def initialize_prior(self, arm: ContentArm,
                         market_evidence: PriorParameters) -> PosteriorState: ...

    def update(self, posterior: PosteriorState, observation: RewardObservation, *,
               effective_weight: float, received_at: datetime) -> PosteriorState: ...

    def posterior_at(self, base: PosteriorState, evidence: Sequence[tuple[float, float]], *,
                     as_of: datetime) -> PosteriorState: ...

    def sample(self, posterior: PosteriorState, rng: random.Random) -> float: ...

    def expected_value(self, posterior: PosteriorState) -> float: ...


@dataclass(frozen=True, slots=True)
class NormalNormalRewardModel:
    """Gaussian posterior over `normalized_reward`; precision-weighted conjugate update."""

    model_type: str = "normal-normal"
    model_version: str = "p2-nn-v1"
    observation_variance: float = 0.09
    variance_floor: float = 1e-6

    def initialize_prior(self, arm: ContentArm,
                         market_evidence: PriorParameters) -> PosteriorState:
        return PosteriorState(
            arm_id=arm.arm_id, model_type=self.model_type, model_version=self.model_version,
            prior_mean=market_evidence.mean, prior_variance=market_evidence.variance,
            posterior_mean=market_evidence.mean, posterior_variance=market_evidence.variance,
            observation_count=0, effective_observation_count=0.0, update_version=0,
            last_update_at=arm.as_of,
        )

    def update(self, posterior: PosteriorState, observation: RewardObservation, *,
               effective_weight: float, received_at: datetime) -> PosteriorState:
        if observation.normalized_reward.state.value != "known":
            raise UnmeasuredRewardError(
                f"observation {observation.key!r} has no measured normalized_reward: an "
                "unmeasured reward is unknown, never zero (ADR-0023 #4)"
            )
        value = observation.normalized_reward.unwrap()
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"normalized_reward {value!r} outside the allowed range [0, 1] (§72)"
            )
        if effective_weight <= 0.0:
            raise ValueError(
                "effective_weight must be > 0: a decayed-to-nothing observation is not an update"
            )
        prior_variance = max(posterior.posterior_variance, self.variance_floor)
        prior_precision = 1.0 / prior_variance
        observation_precision = effective_weight / self.observation_variance
        posterior_precision = prior_precision + observation_precision
        posterior_variance = max(1.0 / posterior_precision, self.variance_floor)
        posterior_mean = posterior_variance * (
            posterior.posterior_mean * prior_precision + value * observation_precision
        )
        return replace(
            posterior,
            posterior_mean=min(1.0, max(0.0, posterior_mean)),
            posterior_variance=posterior_variance,
            observation_count=posterior.observation_count + 1,
            effective_observation_count=posterior.effective_observation_count + effective_weight,
            update_version=posterior.update_version + 1,
            last_update_at=received_at,
        )

    def posterior_at(self, base: PosteriorState, evidence: Sequence[tuple[float, float]], *,
                     as_of: datetime) -> PosteriorState:
        """The posterior from the prior and every effective outcome, in one batch.

        `evidence` is `(normalized_reward, recency_weight_at_as_of)` per effective outcome.
        A batch conjugate combination is exact and order-free, unlike folding clamped
        sequential updates, so the result depends only on the stored evidence and `as_of`
        (ADR-0031). `update_version` is the stored row's; this is a view, not a write.
        """
        prior_variance = max(base.prior_variance, self.variance_floor)
        precision = 1.0 / prior_variance
        weighted_sum = base.prior_mean / prior_variance
        effective = 0.0
        for value, weight in evidence:
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"normalized_reward {value!r} outside [0, 1] (§72)")
            if weight <= 0.0:
                raise ValueError("an effective outcome needs a positive weight")
            precision += weight / self.observation_variance
            weighted_sum += weight * value / self.observation_variance
            effective += weight
        variance = max(1.0 / precision, self.variance_floor)
        return replace(
            base,
            posterior_mean=min(1.0, max(0.0, variance * weighted_sum)),
            posterior_variance=variance,
            observation_count=len(evidence),
            effective_observation_count=effective,
            last_update_at=as_of if evidence else base.last_update_at,
        )

    def sample(self, posterior: PosteriorState, rng: random.Random) -> float:
        """One Thompson draw; clamped to the reward range, state untouched."""
        draw = rng.gauss(posterior.posterior_mean, math.sqrt(posterior.posterior_variance))
        return min(1.0, max(0.0, draw))

    def expected_value(self, posterior: PosteriorState) -> float:
        return posterior.posterior_mean

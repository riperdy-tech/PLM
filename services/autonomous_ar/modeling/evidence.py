# services/autonomous_ar/modeling/evidence.py
"""The posterior as of an instant, recomputed from stored effective outcomes (ADR-0031).

The stored posterior row is a cache. What a draw uses is the prior plus every applied
outcome, each discounted by its age at the draw's own `as_of`. Two cycles over the same
evidence at the same instant therefore draw from the same posterior, and age is never
frozen at whatever moment the last update happened to run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState
from ..modeling.decay import recency_weight
from ..modeling.prior import CHANNEL_BASELINE
from ..modeling.reward_model import RewardModel
from ..storage import repositories

__all__ = [
    "CHANNEL_BASELINE", "MIN_POOLED_DOF", "MIN_RANKED_VIDEOS", "OBSERVATION_VARIANCE_BOUNDS",
    "ChannelRewardScale", "aware", "channel_ranks", "channel_reward_scale",
    "pooled_observation_variance", "posterior_as_of",
]

MIN_RANKED_VIDEOS = 3
"""Below this many measured videos there is nothing to rank against (the first video is
always the median), so P9's raw reward is used. Every posterior is recomputed from the
stored evidence at each draw, so the switch changes the scale of all evidence at once."""

MIN_POOLED_DOF = 3
"""Repeat videos (beyond each arm's first) needed before the observation variance is
estimated; fewer keep the configured default."""

OBSERVATION_VARIANCE_BOUNDS = (0.01, 0.25)
"""An estimate is kept inside these: never certain from a few identical rewards, never
wider than any value in [0, 1] can be."""


@dataclass(frozen=True, slots=True)
class ChannelRewardScale:
    """How rewards enter the posterior (review 2026-11-26, #39).

    ``ranks`` maps an applied observation id to its mid-rank among the brand's applied
    outcomes (empty and ``ranked`` false while the channel is younger than
    :data:`MIN_RANKED_VIDEOS`). ``observation_variance`` is estimated from those ranks.
    """

    ranked: bool
    observation_variance: float
    ranks: Mapping[str, float] = field(default_factory=dict)


def channel_ranks(rewards: Sequence[tuple[str, float]]) -> dict[str, float]:
    """Mid-rank of each reward among all of them, in (0, 1); ties share their rank."""
    values = [value for _, value in rewards]
    count = len(values)
    return {key: (sum(1 for other in values if other < value)
                  + 0.5 * sum(1 for other in values if other == value)) / count
            for key, value in rewards}


def pooled_observation_variance(by_arm: Mapping[str, Sequence[float]], *,
                                default: float) -> float:
    """Pooled within-arm sample variance: how much one arm's videos differ. ``default``
    until :data:`MIN_POOLED_DOF` repeats exist; kept inside the bounds."""
    squares, dof = 0.0, 0
    for values in by_arm.values():
        if len(values) < 2:
            continue
        mean = sum(values) / len(values)
        squares += sum((value - mean) ** 2 for value in values)
        dof += len(values) - 1
    if dof < MIN_POOLED_DOF:
        return default
    low, high = OBSERVATION_VARIANCE_BOUNDS
    return min(high, max(low, squares / dof))


def channel_reward_scale(session: Any, *, brand_id: str, as_of: datetime,
                         default_variance: float) -> ChannelRewardScale:
    """The brand's applied outcomes at ``as_of``, ranked once the channel has enough."""
    rows = repositories.load_channel_applied_evidence(session, brand_id=brand_id,
                                                      as_of=as_of)
    if len(rows) < MIN_RANKED_VIDEOS:
        return ChannelRewardScale(ranked=False, observation_variance=default_variance)
    ranks = channel_ranks([(str(row["id"]), float(row["normalized_reward"])) for row in rows])
    by_arm: dict[str, list[float]] = {}
    for row in rows:
        by_arm.setdefault(str(row["arm_id"]), []).append(ranks[str(row["id"])])
    return ChannelRewardScale(
        ranked=True, ranks=ranks,
        observation_variance=pooled_observation_variance(by_arm, default=default_variance))


def aware(value: datetime) -> datetime:
    """SQLite drops `tzinfo` on read-back; stored instants are aware-UTC (P2.3 convention)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _weight(observed_at: datetime, *, as_of: datetime, policy: PolicyConfig) -> float:
    if not policy.nonstationarity.enabled:
        return 1.0
    return recency_weight(observed_at, now=as_of,
                          half_life_days=policy.nonstationarity.decay_half_life_days)


def posterior_as_of(session: Any, base: PosteriorState, *, model: RewardModel,
                    policy: PolicyConfig, as_of: datetime,
                    scale: ChannelRewardScale | None = None) -> PosteriorState:
    """With a ranked ``scale`` each reward enters as its rank among the channel's own
    videos (an id the scale does not know keeps its raw value); else P9's raw reward."""
    rows = repositories.load_applied_evidence(session, base.arm_id, as_of=as_of)
    ranks = scale.ranks if scale is not None and scale.ranked else {}
    evidence = [
        (ranks.get(str(row["id"]), float(row["normalized_reward"])),
         _weight(aware(row["observed_at"]), as_of=as_of, policy=policy))
        for row in rows
    ]
    return model.posterior_at(base, evidence, as_of=as_of)

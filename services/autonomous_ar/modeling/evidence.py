# services/autonomous_ar/modeling/evidence.py
"""The posterior as of an instant, recomputed from stored effective outcomes (ADR-0031).

The stored posterior row is a cache. What a draw uses is the prior plus every applied
outcome, each discounted by its age at the draw's own `as_of`. Two cycles over the same
evidence at the same instant therefore draw from the same posterior, and age is never
frozen at whatever moment the last update happened to run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState
from ..modeling.decay import recency_weight
from ..modeling.reward_model import RewardModel
from ..storage import repositories

__all__ = ["aware", "posterior_as_of"]


def aware(value: datetime) -> datetime:
    """SQLite drops `tzinfo` on read-back; stored instants are aware-UTC (P2.3 convention)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _weight(observed_at: datetime, *, as_of: datetime, policy: PolicyConfig) -> float:
    if not policy.nonstationarity.enabled:
        return 1.0
    return recency_weight(observed_at, now=as_of,
                          half_life_days=policy.nonstationarity.decay_half_life_days)


def posterior_as_of(session: Any, base: PosteriorState, *, model: RewardModel,
                    policy: PolicyConfig, as_of: datetime) -> PosteriorState:
    rows = repositories.load_applied_evidence(session, base.arm_id, as_of=as_of)
    evidence = [
        (float(row["normalized_reward"]), _weight(aware(row["observed_at"]), as_of=as_of,
                                                   policy=policy))
        for row in rows
    ]
    return model.posterior_at(base, evidence, as_of=as_of)

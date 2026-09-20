"""Posterior state and its provenance (§47, §74). Dumb, validated, frozen records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import UpdateStatus

__all__ = ["PosteriorState", "PriorParameters", "UpdateRecord"]


@dataclass(frozen=True, slots=True)
class PriorParameters:
    """The P1 → prior transform's output (§10); the transform itself is versioned."""

    mean: float
    variance: float
    confidence: float
    prior_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.mean <= 1.0:
            raise ValueError(f"prior mean must be in [0, 1], got {self.mean}")
        if self.variance <= 0.0:
            raise ValueError(f"prior variance must be > 0, got {self.variance}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass(frozen=True, slots=True)
class PosteriorState:
    """One arm's Gaussian posterior (§47); `update_version` is the optimistic lock (§75)."""

    arm_id: str
    model_type: str
    model_version: str
    prior_mean: float
    prior_variance: float
    posterior_mean: float
    posterior_variance: float
    observation_count: int
    effective_observation_count: float
    update_version: int
    last_update_at: datetime
    context_state_json: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("prior_mean", "posterior_mean"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {getattr(self, name)}")
        for name in ("prior_variance", "posterior_variance"):
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")
        if self.observation_count < 0 or self.effective_observation_count < 0.0:
            raise ValueError("observation counts must be >= 0")
        if self.update_version < 0:
            raise ValueError("update_version must be >= 0")
        if self.last_update_at.tzinfo is None:
            raise ValueError("last_update_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class UpdateRecord:
    """§74's persisted update state — the retry-proofing row."""

    reward_observation_id: str
    arm_id: str
    version_before: int
    version_after: int
    status: UpdateStatus
    effective_weight: float
    updated_at: datetime

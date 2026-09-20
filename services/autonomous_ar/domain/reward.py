"""Provider-neutral reward contract (§17/§72) with the C7 value terms reserved (ADR-0023 #4).

R11: P9 owns reward arithmetic — `normalized_reward` and `reward_version` arrive on the
wire. P2 never computes a reward from raw metrics; a missing `normalized_reward` is
`unknown`, and an unmeasured economic term is `unknown`, never zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from plm_contracts import ObservationWindow, QualityStatus, Tri

from .enums import ObservationMaturity

__all__ = [
    "RESERVED_VALUE_TERMS", "RewardObservation", "observation_key", "tri_from_optional",
]

RESERVED_VALUE_TERMS: tuple[str, ...] = (
    "revenue", "subscriber_value", "catalog_value", "spotify_conversion",
    "future_discovery_value", "production_cost",
)
"""ADR-0023 #4: the full Expected Economic Value terms, reserved in the C7 reward schema.
P2 stores them when a producer supplies them and never fabricates them."""


def tri_from_optional(value: float | None) -> Tri[float]:
    """The only NULL→Tri path. `None` is unknown; an explicit `0.0` is a measurement."""
    return Tri.unknown() if value is None else Tri.known(float(value))


def observation_key(experiment_id: str, window: ObservationWindow) -> str:
    """§73's idempotency identity: `experiment_id + observation_window`."""
    return f"{experiment_id}|{window.value}"


@dataclass(frozen=True, slots=True)
class RewardObservation:
    """§72's payload as P2's domain object. `quality_status` resolves the registry's
    C7 gap (`QualityStatus`, P9 §57 states); it is optional because the C7 freeze (W5.0)
    may respell it — the registry amendment moves both sides together (R2)."""

    experiment_id: str
    arm_id: str
    content_id: str
    observation_window: ObservationWindow
    maturity: ObservationMaturity
    observed_at: datetime
    raw_metrics: Mapping[str, Tri[float]]
    normalized_reward: Tri[float]
    reward_version: str
    attribution_confidence: float
    quality_status: QualityStatus | None = None
    economic_terms: Mapping[str, Tri[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if not 0.0 <= self.attribution_confidence <= 1.0:
            raise ValueError(
                f"attribution_confidence must be in [0, 1], got {self.attribution_confidence}"
            )
        unknown = set(self.economic_terms) - set(RESERVED_VALUE_TERMS)
        if unknown:
            raise ValueError(
                f"economic_terms may only carry reserved C7 terms, got unknown keys {sorted(unknown)}"
            )
        if not self.reward_version:
            raise ValueError("reward_version must not be empty (R11: P9 owns the arithmetic)")

    @property
    def key(self) -> str:
        return observation_key(self.experiment_id, self.observation_window)

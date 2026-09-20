"""Decision-side records (§51): what was sampled, and why an arm was turned away."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import ReasonCode

__all__ = ["ArmSample", "ConstraintRejection"]


@dataclass(frozen=True, slots=True)
class ArmSample:
    """One Thompson draw plus the posterior it came from (§67)."""

    arm_id: str
    sampled_reward: float
    posterior_expected_reward: float


@dataclass(frozen=True, slots=True)
class ConstraintRejection:
    """A rejection with a structured reason code (§57); `detail` is for operators."""

    arm_id: str
    reason: ReasonCode
    detail: str

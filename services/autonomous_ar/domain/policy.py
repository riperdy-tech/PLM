"""The versioned allocation policy (§49/§90/§91) with ADR-0023's arithmetic rules baked in.

Validation is §91's list plus the freeze: fractions >= 0 and <= 1, their sum must not
exceed 1.0 (a remainder is reserved capacity, not an error), `baseline_fraction` is a
residual *inside* the exploit slice and may never exceed it, and half-lives/slot-adjacent
knobs must be positive.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DEFAULT_POLICY", "FatigueConfig", "NonstationarityConfig", "PolicyConfig",
    "RetirementConfig",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FatigueConfig(_Frozen):
    enabled: bool = True
    decay_half_life_days: Annotated[float, Field(gt=0.0)] = 14.0
    max_penalty: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    cooldown_hours: Annotated[float, Field(ge=0.0)] = 72.0


class NonstationarityConfig(_Frozen):
    enabled: bool = True
    decay_half_life_days: Annotated[float, Field(gt=0.0)] = 45.0
    regime_relative_threshold: Annotated[float, Field(gt=0.0, le=1.0)] = 0.30


class RetirementConfig(_Frozen):
    min_mature_observations: Annotated[int, Field(ge=1)] = 5
    weak_posterior_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.25


class PolicyConfig(_Frozen):
    """§49's row as a validated value object; persisted rows are immutable (§92).

    Deliberate omission (recorded by the P2.15 gap audit): §15's cap list also names
    `visual_world`, and this config has no such field. `ar_allocation_policy` (migration
    `0008_ar_initial`, frozen) has no column to persist it, and a share that is not
    persisted and versioned would be a code constant wearing a config's name — §15 forbids
    hard-coding, §92 forbids silently changing a stored policy. Adding it is a new
    migration plus a new policy version, not a patch; the walk already buckets an unset
    dimension under "unknown" whenever a cap does exist (`constraints.py`), so this is a
    persistence-boundary omission, not an unhandled data case.
    """

    name: str = "default"
    version: str = "p2-v1"
    exploit_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.70
    adjacent_explore_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.20
    radical_explore_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10
    baseline_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10
    max_exact_arm_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.50
    max_cluster_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.60
    max_genre_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.60
    max_use_case_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.60
    max_language_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.85
    max_concurrent_experiments_per_cluster: Annotated[int, Field(ge=1)] = 2
    max_market_data_age_hours: Annotated[float, Field(gt=0.0)] = 168.0
    min_measured_components: Annotated[int, Field(ge=2)] = 2
    min_attribution_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    assumed_cost_per_directive: Annotated[float, Field(gt=0.0)] = 1.0
    directive_ttl_hours: Annotated[float, Field(gt=0.0)] = 168.0
    exploration_boost: Annotated[float, Field(ge=0.0, le=0.3)] = 0.10
    value_model: str = "early-stage-proxy-v1"
    fatigue: FatigueConfig = Field(default_factory=FatigueConfig)
    nonstationarity: NonstationarityConfig = Field(default_factory=NonstationarityConfig)
    retirement: RetirementConfig = Field(default_factory=RetirementConfig)

    @model_validator(mode="after")
    def _validate_fractions_and_baseline(self) -> PolicyConfig:
        total = (self.exploit_fraction + self.adjacent_explore_fraction
                 + self.radical_explore_fraction)
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"exploration fractions must not exceed 1.0, got {round(total, 6)} "
                "(ADR-0023 #3; §13 governs §90)"
            )
        if self.baseline_fraction > self.exploit_fraction + 1e-9:
            raise ValueError(
                f"baseline_fraction ({self.baseline_fraction}) may not exceed "
                f"exploit_fraction ({self.exploit_fraction}): it is the residual inside "
                "the exploit slice (ADR-0023 #3)"
            )
        return self


DEFAULT_POLICY = PolicyConfig(name="default")

"""The versioned allocation policy (§49/§90/§91) with ADR-0023's arithmetic rules baked in.

Validation is §91's list plus the freeze: fractions >= 0 and <= 1 and their sum is
exactly 1.0 (ADR-0023 #3; a smaller sum would leave slots allocated to nobody),
`baseline_fraction` is a residual *inside* the exploit slice and may never exceed it, and
half-lives/slot-adjacent knobs must be positive. `outcome_horizon` is the one observation
window whose mature outcome may move a posterior (ADR-0031).
"""

from __future__ import annotations

from typing import Annotated

from plm_contracts import ObservationWindow
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DEFAULT_POLICY", "SINGLE_IDENTITY_POLICY", "FatigueConfig", "NonstationarityConfig",
    "PolicyConfig", "RetirementConfig",
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
    outcome_horizon: ObservationWindow = ObservationWindow.D28
    fatigue: FatigueConfig = Field(default_factory=FatigueConfig)
    nonstationarity: NonstationarityConfig = Field(default_factory=NonstationarityConfig)
    retirement: RetirementConfig = Field(default_factory=RetirementConfig)

    @model_validator(mode="after")
    def _validate_fractions_and_baseline(self) -> PolicyConfig:
        total = (self.exploit_fraction + self.adjacent_explore_fraction
                 + self.radical_explore_fraction)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"exploit + adjacent + radical fractions must sum to 1.0, got {round(total, 6)} "
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

SINGLE_IDENTITY_POLICY = PolicyConfig(
    name="single-identity",
    version="p2-v2",
    max_genre_share=1.0,
    max_use_case_share=1.0,
    max_language_share=1.0,
)
"""`p2-v2`: for a channel with one locked identity (ADR-0044). Every eligible arm shares
the use case (`study_focus`/`focus`), the genre (`focus_electronic` or unset) and the
language (instrumental), so `p2-v1`'s 0.60 genre/use-case caps and 0.85 language cap
would cap a 10-slot cycle at 6 selections however many arms qualify. Those three caps
are lifted; diversity is kept by the unchanged cluster share (0.60) and the per-cluster
concurrency limit (2). `p2-v1` is untouched (§92): a stored version never changes. This
constant is not inserted or activated anywhere; a cycle uses it only once a row for
`p2-v2` is stored and the cycle asks for that version."""

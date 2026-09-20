"""§71's request/response shapes. `extra="forbid"` everywhere so drift is a 422, not a drop.

`CycleCreateRequest.brand_id` is the cycle's coordinate (ADR-0019): §71's draft says
`channel_id`, but `channel_id` is not a wire name — the request model refuses it rather
than aliasing the two. `GET /ar/directives` has no schema here: its `response_model` is
`ProductionDirective` itself, the frozen C2 payload, so the wire cannot be transcribed
twice. A missing value is `null`, never a fabricated `0.0` (the arm-state posterior block
is `None` until a posterior row exists).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from plm_contracts import ChannelActivationState
from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "ArmStateOut", "CycleCreateRequest", "CycleSummaryOut", "DecisionOut", "ResultAccepted",
    "UpdateSummaryOut",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CycleCreateRequest(_Frozen):
    """§71's create body, with ADR-0019's coordinate and the §117 activation override."""

    brand_id: str = Field(min_length=1)
    slot_count: Annotated[int, Field(ge=1)]
    budget: Annotated[float, Field(gt=0.0)]
    as_of: datetime
    policy_version: str = Field(min_length=1)
    activation_state: ChannelActivationState | None = None
    random_seed: int | None = None
    unavailable_capabilities: list[str] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def _as_of_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("as_of must be timezone-aware: it is the cycle's persisted instant")
        return value


class CycleSummaryOut(_Frozen):
    """The cycle's stored counts. `replay` is meaningful on POST (a re-request returned the
    stored cycle); a GET always reads the stored cycle and reports `replay=False`."""

    cycle_id: str
    status: str
    replay: bool
    activation_state: str
    slot_count: Annotated[int, Field(ge=1)]
    budget: float
    p1_snapshot_time: datetime
    selected_count: Annotated[int, Field(ge=0)]
    decision_count: Annotated[int, Field(ge=0)]
    rejected_count: Annotated[int, Field(ge=0)]
    warning_codes: list[str]


class DecisionOut(_Frozen):
    """One stored decision row (§76's version strings included); ranks are `None` when the
    arm was flagged ineligible, never a fabricated position."""

    decision_id: str
    cycle_id: str
    arm_id: str
    allocation_type: str
    sampled_reward: float
    posterior_expected_reward: float
    selection_priority: float
    rank_before_constraints: int | None
    rank_after_constraints: int | None
    constraint_adjustments: dict[str, Any]
    reason_codes: list[str]
    policy_version: str
    model_version: str
    feature_version: str
    normalization_version: str
    p1_score_version: str | None
    created_at: datetime


class ArmStateOut(_Frozen):
    """§71's arm state + posterior summary. The posterior block is `None` while no posterior
    row exists — absent stays distinguishable from a measured zero."""

    arm_id: str
    brand_id: str
    status: str
    selection_count: Annotated[int, Field(ge=0)]
    mature_result_count: Annotated[int, Field(ge=0)]
    provisional_result_count: Annotated[int, Field(ge=0)]
    recent_exposure_score: float
    cooldown_until: datetime | None
    retirement_reason: str | None
    last_updated_at: datetime
    state_version: Annotated[int, Field(ge=1)]
    posterior_model_version: str | None
    posterior_mean: float | None
    posterior_variance: float | None
    observation_count: int | None
    update_version: int | None


class ResultAccepted(_Frozen):
    observation_id: str
    experiment_result_id: str
    stored: bool


class UpdateSummaryOut(_Frozen):
    """§74's counters: `applied` is the number of new posterior updates in this call."""

    applied: Annotated[int, Field(ge=0)]
    skipped_duplicate: Annotated[int, Field(ge=0)]
    quarantined: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]

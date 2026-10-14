"""Seam C7 · PHASE-P8 → PHASE-P9 → PHASE-P2: the feedback loop (R9, R10, R11, ADR-0030).

Leg 1, :class:`DistributionRecordPayload`: P8's stable record of a submitted release,
with the lineage it was submitted under. P9 links each ISRC to the release and lineage
so that statement revenue can be attributed to the arm.

Leg 2, :class:`RewardResultPayload`: P9's normalized reward for one experiment outcome,
posted to P2's ``POST /ar/results``. The vocabulary is shared:

* ``observation_window`` is :class:`~plm_contracts.enums.ObservationWindow` (P9 used
  ``1h``/``14d``, which P2 refused);
* ``status`` is :class:`ObservationMaturity`;
* a raw metric that was not measured is ``null``, never ``0``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ..enums import ObservationWindow, QualityStatus
from .base import SeamModel
from .lineage import ExperimentLineage

__all__ = [
    "RAW_REWARD_METRICS",
    "DeliveryRecord",
    "DistributionRecordPayload",
    "ObservationMaturity",
    "RewardResultPayload",
]

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Isrc = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{2}[0-9]{5}$")]


class ObservationMaturity(StrEnum):
    """P2 §18/§19: a 24h result is provisional, never final."""

    PROVISIONAL = "provisional"
    MATURE = "mature"
    CLOSED = "closed"


class DeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    store: NonEmpty
    external_release_id: str | None
    """``None`` until the store reports its id."""
    status: NonEmpty
    first_delivered_at: AwareDatetime | None
    """``None`` until first delivered."""


class DistributionRecordPayload(SeamModel):
    """Leg 1: one submitted release and the lineage it is attributed to."""

    SEAM: ClassVar[str] = "C7/leg1"
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    release_id: NonEmpty
    artist_id: NonEmpty
    release_version: int = Field(ge=1)
    isrcs: tuple[Isrc, ...] = Field(min_length=1)
    provider: NonEmpty
    deliveries: tuple[DeliveryRecord, ...]
    source_package_hash: NonEmpty
    status: NonEmpty
    lineage: ExperimentLineage
    media_package_id: NonEmpty


RAW_REWARD_METRICS: Final[tuple[str, ...]] = (
    "views",
    "watch_time_minutes",
    "average_percentage_viewed",
    "subscriber_gain",
    "estimated_revenue",
)
"""The registry's raw-metric keys (P2 §72). Each must be present; ``null`` = not measured."""


class RewardResultPayload(SeamModel):
    """Leg 2: P9's normalized reward for one outcome, posted to ``POST /ar/results``."""

    SEAM: ClassVar[str] = "C7/leg2"
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    experiment_id: NonEmpty
    arm_id: NonEmpty
    content_id: NonEmpty
    observation_window: ObservationWindow
    status: ObservationMaturity
    observed_at: AwareDatetime
    raw_metrics: dict[str, float | None]
    normalized_reward: float = Field(ge=0.0, le=1.0)
    reward_version: NonEmpty
    attribution_confidence: float = Field(ge=0.0, le=1.0)
    quality_status: QualityStatus | None = None
    """Optional (the C7 freeze left it open); ``None`` means P9 did not assess quality."""
    economic_terms: dict[str, float | None] | None = None
    """Reserved for the full economic-value terms (ADR-0023 #4); P2 validates the names."""

    @model_validator(mode="after")
    def _registry_metric_keys(self) -> Self:
        if set(self.raw_metrics) != set(RAW_REWARD_METRICS):
            raise ValueError(
                f"raw_metrics must have exactly the keys {list(RAW_REWARD_METRICS)}; "
                f"got {sorted(self.raw_metrics)}"
            )
        return self

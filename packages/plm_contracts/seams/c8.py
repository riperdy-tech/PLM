"""Seam C8 · PHASE-P9 → PHASE-P10: the feature view (deferred consumer, R12, ADR-0025).

P10 is deferred, but the seam is frozen so that it can be built later against a stable,
honest view. Every measured value is ``null`` when it was not measured or assessed. The
view used to serve ``0`` views and streams, ``1.0`` completeness and fidelity,
``contamination=false`` and a hardcoded ``REPORTED`` status for outcomes nobody had
measured.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..enums import ObservationWindow
from .base import SeamModel
from .c7 import ObservationMaturity

__all__ = ["P10FeatureViewPayload", "PrimaryOutcome", "QualityView", "SecondaryOutcomes"]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PrimaryOutcome(_Closed):
    metric: str = Field(min_length=1)
    value: float
    status: ObservationMaturity
    """How mature the primary measurement is; the same scale P2 learns from."""


class SecondaryOutcomes(_Closed):
    """``None`` = not measured for this unit, never zero."""

    views: float | None
    watch_time_hours: float | None
    subscribers_gained: float | None
    spotify_streams: float | None


class QualityView(_Closed):
    """``None`` = not assessed. ``attribution_confidence`` is always known."""

    data_completeness: float | None = Field(ge=0.0, le=1.0)
    fidelity: float | None = Field(ge=0.0, le=1.0)
    contamination: bool | None
    attribution_confidence: float = Field(ge=0.0, le=1.0)


class P10FeatureViewPayload(SeamModel):
    """One unit's outcome features (P9 §155)."""

    SEAM: ClassVar[str] = "C8"
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    unit_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    arm_id: str = Field(min_length=1)
    content_genome_id: str = Field(min_length=1)
    observation_window: ObservationWindow
    maturity: ObservationMaturity
    primary_outcome: PrimaryOutcome
    secondary_outcomes: SecondaryOutcomes
    quality: QualityView

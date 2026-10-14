"""Request and response schemas for `/analytics` API (P9 §88, Ruling R10, R11)."""

from __future__ import annotations

from typing import Any

from plm_contracts import ObservationWindow, QualityStatus
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c7 import ObservationMaturity
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "healthy"
    service: str = "analytics"
    timestamp: str


class YouTubeIngestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    video_id: str
    channel_id: str
    date_str: str
    views: int
    watch_time_minutes: float
    average_view_duration_seconds: float = 0.0
    average_percentage_viewed: float = 0.0
    subscribers_gained: int = 0
    subscribers_lost: int = 0
    estimated_revenue_usd: float = 0.0
    window: str = "24h"
    observed_at: AwareDatetime


class DistributorIngestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str
    period: str
    raw_content: str
    source_reference: str
    rows: list[dict[str, Any]] | None = None


class CostIngestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entity_id: str
    entity_type: str
    category: str
    amount_usd: float
    period: str
    description: str = ""
    incurred_at: AwareDatetime


class FeedbackRequest(BaseModel):
    """Evaluate one outcome and build its C7 leg-2 reward (ADR-0030).

    Every measurement fact is explicit. An unmeasured raw metric is ``null``, never 0.
    """

    model_config = ConfigDict(extra="forbid")

    lineage: ExperimentLineage
    unit_id: str = Field(min_length=1)
    net_contribution_usd: float
    views: int | None
    watch_time_minutes: float | None
    average_percentage_viewed: float | None
    subscribers_gained: int | None
    estimated_revenue: float | None
    window: ObservationWindow
    maturity: ObservationMaturity
    attribution_confidence: float = Field(ge=0.0, le=1.0)
    quality_status: QualityStatus | None
    observed_at: AwareDatetime

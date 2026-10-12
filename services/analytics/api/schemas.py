"""Request and response schemas for `/analytics` API (P9 §88, Ruling R10, R11)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


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


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    experiment_id: str
    experiment_family_id: str
    content_arm_id: str
    content_genome_id: str
    unit_id: str
    net_contribution_usd: float
    views: int = 0
    watch_time_minutes: float = 0.0
    window: str = "7d"

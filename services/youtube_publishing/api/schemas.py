"""Pydantic schemas for `/youtube` API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PublishRequest(BaseModel):
    """Request to publish a P6 media package to YouTube."""

    model_config = ConfigDict(extra="forbid")

    media_package: dict[str, Any]
    channel_id: str = "UC_neon_records"
    operating_mode: str = "dry_run"
    publish_at: datetime | None = None


class PublishResponse(BaseModel):
    """Response returned upon initiation/completion of publishing."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    manifest_id: str
    state: str
    youtube_video_id: str | None = None
    video_url: str | None = None
    error: str | None = None


class AttemptStatusResponse(BaseModel):
    """Response detailing the current status of a publishing attempt."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    manifest_id: str
    channel_id: str
    state: str
    operating_mode: str
    youtube_video_id: str | None = None
    bytes_uploaded: int = 0
    total_bytes: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ReconcileResponse(BaseModel):
    """Response returned when reconciling a video status."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    state: str
    youtube_video_id: str | None = None
    remote_status: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """Healthcheck response."""

    model_config = ConfigDict(extra="forbid")

    status: str
    version: str = "v1"
    timestamp: datetime

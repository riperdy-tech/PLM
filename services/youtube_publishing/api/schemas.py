"""Pydantic schemas for `/youtube` API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PublishRequest(BaseModel):
    """Request to publish a P6 media package to YouTube."""

    model_config = ConfigDict(extra="forbid")

    media_package: dict[str, Any]
    """The Seam C6 hop-1 payload, parsed with the shared model (ADR-0029)."""
    channel_id: str = Field(min_length=1)
    """A channel profile configured by an operator (P0 §10); never auto-created."""
    operating_mode: str = "dry_run"
    publish_at: AwareDatetime | None = None
    """Must carry a timezone; a naive time is ambiguous and is refused (422)."""


class PublishResponse(BaseModel):
    """Response returned upon initiation/completion of publishing."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    manifest_id: str
    state: str
    youtube_video_id: str | None = None
    video_url: str | None = None
    error: str | None = None
    publication_event: dict[str, Any] | None = None
    """The C6 hop-2 ``youtube_video_published`` wire once published (ADR-0029)."""


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

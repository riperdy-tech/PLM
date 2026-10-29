"""Pydantic schemas for `/youtube` API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..domain.enums import OperatingMode, PrivacyStatus


class PublishRequest(BaseModel):
    """Request to publish a P6 media package to YouTube."""

    model_config = ConfigDict(extra="forbid")

    media_package: dict[str, Any]
    """The Seam C6 hop-1 payload, parsed with the shared model (ADR-0029)."""
    channel_id: str = Field(min_length=1)
    """A channel profile configured by an operator (P0 §10); never auto-created."""
    operating_mode: str = "dry_run"
    metadata_variant_id: str | None = Field(default=None, min_length=1)
    thumbnail_id: str | None = Field(default=None, min_length=1)
    """Explicit P7 selection IDs; omitted IDs retain first-declared compatibility."""
    publish_at: AwareDatetime | None = None
    """Must carry a timezone; a naive time is ambiguous and is refused (422)."""


class PublishResponse(BaseModel):
    """Response returned upon initiation/completion of publishing."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    manifest_id: str
    candidate_selection: dict[str, Any] | None = None
    state: str
    youtube_video_id: str | None = None
    video_url: str | None = None
    error: str | None = None
    publication_event: dict[str, Any] | None = None
    """The C6 hop-2 ``youtube_video_published`` wire once published (ADR-0029)."""
    published_at: datetime | None = None
    external_writes: list[dict[str, str]] = Field(default_factory=list)
    """One receipt per external write the publication comprises: ``capability``,
    ``idempotency_key``, the guard's ``decision`` and ``reason`` (R5). The spend of an
    executed write is the ledger entry under (capability, key)."""


class AttemptStatusResponse(BaseModel):
    """Response detailing the current status of a publishing attempt."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    manifest_id: str
    candidate_selection: dict[str, Any] | None = None
    channel_id: str
    state: str
    operating_mode: str
    youtube_video_id: str | None = None
    thumbnail_uploaded: bool = False
    """P7 upload receipt, not proof of user impressions or on-screen exposure."""
    bytes_uploaded: int = 0
    total_bytes: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    published_at: datetime | None = None
    """From the publication record, once the attempt has published."""
    publication_event: dict[str, Any] | None = None


class VideoAnalyticsResponse(BaseModel):
    """One cumulative snapshot of a published video for one window (R5).

    ``provider_mode`` says where the numbers come from; ``mock`` numbers are a simulation
    (:mod:`plm_platform.testing.mock_outcomes`), never a real audience.
    """

    model_config = ConfigDict(extra="forbid")

    video_id: str
    channel_id: str
    content_arm_id: str
    window: str
    published_at: datetime
    observed_at: datetime
    provider_mode: str
    source: str
    model_version: str | None = None
    metrics: dict[str, Any]


class ChannelProfileRequest(BaseModel):
    """An operator's channel configuration (``PUT /youtube/channels/{channel_id}``, P0 §10).

    ``oauth_credential_ref`` names where the credential lives; it is never the secret.
    ``default_made_for_kids`` is an audience declaration the operator must make (no
    default); the remaining fields default to the profile's conservative values (private,
    ``dry_run``, synthetic media disclosed).
    """

    model_config = ConfigDict(extra="forbid")

    channel_name: str = Field(min_length=1, max_length=255)
    brand_id: str = Field(min_length=1, max_length=64)
    oauth_credential_ref: str = Field(min_length=1, max_length=128)
    default_made_for_kids: bool
    default_language: str = Field(default="en", min_length=1, max_length=16)
    default_country: str = Field(default="US", min_length=1, max_length=8)
    default_category_id: str = Field(default="10", min_length=1, max_length=16)
    default_privacy_mode: PrivacyStatus = PrivacyStatus.PRIVATE
    default_contains_synthetic_media: bool = True
    default_publish_timezone: str = Field(default="UTC", min_length=1, max_length=64)
    active: bool = True
    operating_mode: OperatingMode = OperatingMode.DRY_RUN


class ChannelProfileResponse(BaseModel):
    """The stored profile after the ``PUT``; ``created`` is true on the first write."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    channel_id: str
    channel_name: str
    brand_id: str
    oauth_credential_ref: str
    default_made_for_kids: bool
    default_language: str
    default_country: str
    default_category_id: str
    default_privacy_mode: str
    default_contains_synthetic_media: bool
    default_publish_timezone: str
    active: bool
    operating_mode: str
    created_at: datetime
    updated_at: datetime
    created: bool


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

"""Domain models for Phase P7 Autonomous YouTube Publishing Engine (P7 §8, §9, §10, Ruling R9)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import OperatingMode, PrivacyStatus, PublishState


class ChannelProfile(BaseModel):
    """Configured target YouTube channel profile (P7 §8.1)."""

    model_config = ConfigDict(frozen=True)

    id: str
    channel_id: str
    channel_name: str
    brand_id: str
    oauth_credential_ref: str
    default_language: str = "en"
    default_country: str = "US"
    default_category_id: str = "10"  # Music
    default_privacy_mode: PrivacyStatus = PrivacyStatus.PRIVATE
    default_made_for_kids: bool = False
    default_contains_synthetic_media: bool = True
    default_publish_timezone: str = "UTC"
    active: bool = True
    operating_mode: OperatingMode = OperatingMode.DRY_RUN
    created_at: datetime
    updated_at: datetime


class PreflightCheckResult(BaseModel):
    """Result of preflight validation gates A, B, and C (P7 §13)."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    gate_name: str
    diagnostics: list[str] = Field(default_factory=list)
    checked_at: datetime


class PublishManifest(BaseModel):
    """Immutable frozen publish manifest (P7 §10)."""

    model_config = ConfigDict(frozen=True)

    manifest_id: str
    channel_id: str
    media_package_id: str
    release_manifest_id: str
    experiment_id: str
    experiment_family_id: str
    content_arm_id: str
    content_genome_id: str
    production_directive_id: str
    brand_id: str
    idempotency_key: str
    video_master_uri: str
    video_checksum: str
    video_duration_seconds: float
    thumbnail_uri: str
    thumbnail_checksum: str
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    category_id: str = "10"
    privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    publish_at: datetime | None = None
    contains_synthetic_media: bool = True
    made_for_kids: bool = False
    playlist_id: str | None = None
    playlist_title: str | None = None
    metadata_version: str = "v1"
    created_at: datetime


class PublishAttempt(BaseModel):
    """A durable execution attempt of a publish manifest (P7 §12)."""

    model_config = ConfigDict(frozen=True)

    attempt_id: str
    manifest_id: str
    channel_id: str
    idempotency_key: str
    state: PublishState = PublishState.RECEIVED
    operating_mode: OperatingMode = OperatingMode.DRY_RUN
    upload_session_uri: str | None = None
    youtube_video_id: str | None = None
    youtube_playlist_id: str | None = None
    youtube_playlist_item_id: str | None = None
    thumbnail_uploaded: bool = False
    bytes_uploaded: int = 0
    total_bytes: int = 0
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class YouTubePublicationRecord(BaseModel):
    """Authoritative publication record linking local release to YouTube state (P7 §8, §65)."""

    model_config = ConfigDict(frozen=True)

    publication_record_id: str
    youtube_video_id: str
    channel_id: str
    publish_attempt_id: str
    media_package_id: str
    release_manifest_id: str
    experiment_id: str
    experiment_family_id: str
    content_arm_id: str
    content_genome_id: str
    production_directive_id: str
    title: str
    privacy_status: PrivacyStatus
    contains_synthetic_media: bool
    published_at: datetime
    video_url: str
    thumbnail_url: str | None = None
    playlist_id: str | None = None
    created_at: datetime
    updated_at: datetime


class YouTubeVideoPublishedEvent(BaseModel):
    """Hop 2 event emitted upon successful publication (P7 §65, Ruling R9).

    Must carry all R9 identity fields to close the learning loop at P9.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: str = "youtube_video_published"
    youtube_video_id: str
    publish_attempt_id: str
    experiment_id: str
    content_arm_id: str
    published_at: datetime
    metadata_version: str
    experiment_family_id: str  # R9
    content_genome_id: str  # R9
    production_directive_id: str  # R9
    media_package_id: str  # R9

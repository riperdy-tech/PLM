"""SQLAlchemy ORM models for Phase P7 Autonomous YouTube Publishing Engine (P7 §8, Ruling R9)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


YOUTUBE_METADATA = Base.metadata



class ChannelProfileRow(Base):
    __tablename__ = "channel_profiles"
    __table_args__ = {"schema": "youtube"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    channel_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    brand_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    oauth_credential_ref: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    default_language: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="en")
    default_country: Mapped[str] = mapped_column(sa.String(8), nullable=False, default="US")
    default_category_id: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="10")
    default_privacy_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="private")
    default_made_for_kids: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    default_contains_synthetic_media: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    default_publish_timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="UTC")
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    operating_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="dry_run")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class PublishManifestRow(Base):
    __tablename__ = "publish_manifests"
    __table_args__ = {"schema": "youtube"}  # noqa: RUF012

    manifest_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    media_package_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    release_manifest_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    experiment_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    experiment_family_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_arm_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_genome_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    production_directive_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    brand_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    video_master_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    video_checksum: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    video_duration_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False)
    thumbnail_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    thumbnail_checksum: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    tags: Mapped[Any] = mapped_column(sa.JSON, nullable=False)
    category_id: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="10")
    privacy_status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    publish_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    contains_synthetic_media: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    made_for_kids: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    playlist_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    playlist_title: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    metadata_version: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="v1")
    # NULL on historical manifests: exposure/selection provenance cannot be inferred.
    selection_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    # NULL only on manifests stored before migration 0033; they are never cleared.
    tracks_json: Mapped[list[dict[str, str]] | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class PublishAttemptRow(Base):
    __tablename__ = "publish_attempts"
    __table_args__ = {"schema": "youtube"}  # noqa: RUF012

    attempt_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    state: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    operating_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    upload_session_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    youtube_playlist_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    youtube_playlist_item_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    thumbnail_uploaded: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    bytes_uploaded: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class PublicationRecordRow(Base):
    __tablename__ = "publication_records"
    __table_args__ = {"schema": "youtube"}  # noqa: RUF012

    publication_record_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    youtube_video_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    channel_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    publish_attempt_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    media_package_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    release_manifest_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    experiment_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    experiment_family_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_arm_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_genome_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    production_directive_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    privacy_status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    contains_synthetic_media: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    published_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    video_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    playlist_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

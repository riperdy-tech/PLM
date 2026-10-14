"""SQLAlchemy ORM models for Phase P8 DSP Distribution Engine (P8 §6, §7, §8, Ruling R9, R10)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


DISTRIBUTION_METADATA = Base.metadata


class ArtistRow(Base):
    __tablename__ = "artists"
    __table_args__ = {"schema": "distribution"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    artist_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="virtual")
    brand_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    spotify_artist_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    apple_artist_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class ReleaseRow(Base):
    __tablename__ = "releases"
    __table_args__ = {"schema": "distribution"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    release_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    artist_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    release_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="single")
    release_title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    release_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True, default="draft")
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="mock_distributor")
    provider_release_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    submission_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    deliveries: Mapped[Any] = mapped_column(sa.JSON, nullable=False)
    source_manifest_ids: Mapped[Any] = mapped_column(sa.JSON, nullable=False)
    experiment_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    experiment_family_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_arm_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_genome_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    production_directive_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    media_package_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class TrackRow(Base):
    __tablename__ = "tracks"
    __table_args__ = {"schema": "distribution"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    track_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    release_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    track_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    disc_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    version_title: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    isrc: Mapped[str] = mapped_column(sa.String(32), nullable=False, unique=True, index=True)
    isrc_source: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="internal_allocation")
    duration_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False)
    audio_asset_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    audio_checksum: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    explicit: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class IsrcAllocationRow(Base):
    """One ISRC per recording, allocated once and never regenerated (ADR-0029)."""

    __tablename__ = "isrc_allocations"
    __table_args__ = {"schema": "distribution"}  # noqa: RUF012

    recording_key: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    isrc: Mapped[str] = mapped_column(sa.String(32), nullable=False, unique=True)
    country: Mapped[str] = mapped_column(sa.String(2), nullable=False)
    registrant: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    year: Mapped[str] = mapped_column(sa.String(2), nullable=False)
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class ProviderReportRow(Base):
    """Raw distributor report row (Ruling R10)."""

    __tablename__ = "provider_reports"
    __table_args__ = {"schema": "distribution"}  # noqa: RUF012

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    provider_period: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    report_checksum: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    source_reference: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    raw_payload_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    report_type: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="streaming_statement")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

"""Domain models for Phase P8 DSP / Music Distribution Engine (P8 §6, §7, §8, §71, Ruling R9, R10)."""

from __future__ import annotations

from datetime import datetime

from plm_contracts.seams.c7 import DistributionRecordPayload as SharedDistributionRecordPayload
from pydantic import BaseModel, ConfigDict, Field

from .enums import ArtistType, DeliveryStatus, ISRCSource, ReleaseStatus, ReleaseType, StorePlatform


class ArtistProfile(BaseModel):
    """Artist identity object (P8 §9)."""

    model_config = ConfigDict(frozen=True)

    id: str
    canonical_name: str
    normalized_name: str
    artist_type: ArtistType = ArtistType.VIRTUAL
    brand_id: str
    spotify_artist_id: str | None = None
    apple_artist_id: str | None = None
    created_at: datetime
    updated_at: datetime


class DistributionTrack(BaseModel):
    """Canonical track record within a distribution release (P8 §7)."""

    model_config = ConfigDict(frozen=True)

    id: str
    track_id: str
    release_id: str
    track_number: int
    disc_number: int = 1
    title: str
    version_title: str | None = None
    isrc: str
    isrc_source: ISRCSource = ISRCSource.INTERNAL_ALLOCATION
    duration_seconds: float
    audio_asset_uri: str
    audio_checksum: str
    explicit: bool = False
    created_at: datetime


class DeliveryTarget(BaseModel):
    """Store delivery observation (P8 §6, §71)."""

    model_config = ConfigDict(frozen=True)

    store: StorePlatform
    external_release_id: str | None = None
    status: DeliveryStatus = DeliveryStatus.PENDING
    first_delivered_at: datetime | None = None


class CanonicalReleasePackage(BaseModel):
    """Immutable compiled source package for a distribution release (P8 §11)."""

    model_config = ConfigDict(frozen=True)

    release_id: str
    release_version: int = 1
    artist_id: str
    artist_name: str
    brand_id: str
    release_type: ReleaseType = ReleaseType.SINGLE
    title: str
    label_name: str = "Neon Records"
    copyright_line: str = "2026 Neon Records"
    publisher_line: str = "2026 Neon Music Publishing"
    release_date: str  # YYYY-MM-DD
    tracks: list[DistributionTrack]
    artwork_asset_uri: str
    artwork_checksum: str
    territories: str = "WORLDWIDE"
    source_manifest_ids: list[str] = Field(default_factory=list)  # ["p5_...", "p6_..."]
    submission_fingerprint: str
    created_at: datetime


class DistributionRelease(BaseModel):
    """Authoritative release state in the distribution control plane (P8 §6)."""

    model_config = ConfigDict(frozen=True)

    id: str
    release_id: str
    artist_id: str
    release_type: ReleaseType
    release_title: str
    release_version: int
    status: ReleaseStatus = ReleaseStatus.DRAFT
    provider: str = "mock_distributor"
    provider_release_id: str | None = None
    submission_fingerprint: str
    deliveries: list[DeliveryTarget] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DistributionReportReference(BaseModel):
    """Raw distributor report reference.

    Ruling R10: P8 stores and versions raw provider reports and exposes
    references. P8 MUST NOT compute revenue.
    """

    model_config = ConfigDict(frozen=True)

    report_id: str
    provider: str
    provider_period: str
    received_at: datetime
    report_checksum: str
    source_reference: str
    raw_payload_uri: str
    report_type: str = "streaming_statement"


DistributionRecordPayload = SharedDistributionRecordPayload
"""Seam C7 leg 1 (P8 §71, R9, R10): the shared ``plm_contracts.seams.c7`` model."""

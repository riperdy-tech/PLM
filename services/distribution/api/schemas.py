"""Request and response schemas for distribution API (P8 §71, Ruling R9, R10)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReleaseSubmitRequest(BaseModel):
    """Payload to compile and submit a release for distribution."""

    model_config = ConfigDict(extra="ignore")

    p5_manifest_id: str
    p6_media_package_id: str
    artist_name: str
    artist_id: str | None = None
    brand_id: str | None = None
    release_title: str | None = None
    release_version: int = 1
    release_date: str | None = None
    label_name: str = "Neon Records"
    copyright_line: str = "2026 Neon Records"
    publisher_line: str = "2026 Neon Music Publishing"
    territories: str = "WORLDWIDE"
    tracks: list[dict[str, Any]] | None = None
    # R9 identity fields:
    experiment_id: str = "exp_unknown"
    experiment_family_id: str = "fam_unknown"
    content_arm_id: str = "arm_unknown"
    content_genome_id: str = "gen_unknown"
    production_directive_id: str = "dir_unknown"
    media_package_id: str = "pkg_unknown"


class ReleaseResponse(BaseModel):
    """Response describing distribution release state and delivery targets."""

    model_config = ConfigDict(extra="ignore")

    release_id: str
    artist_id: str
    release_type: str
    release_title: str
    release_version: int
    status: str
    provider: str
    provider_release_id: str | None
    submission_fingerprint: str
    deliveries: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ReportIngestRequest(BaseModel):
    """Payload to ingest a raw distributor report (Ruling R10)."""

    model_config = ConfigDict(extra="ignore")

    provider: str
    provider_period: str
    raw_content: str
    source_reference: str
    raw_payload_uri: str | None = None
    report_type: str = "streaming_statement"


class ReportReferenceResponse(BaseModel):
    """Response referencing an ingested raw distributor report (Ruling R10)."""

    model_config = ConfigDict(extra="ignore")

    report_id: str
    provider: str
    provider_period: str
    report_checksum: str
    source_reference: str
    raw_payload_uri: str
    report_type: str
    received_at: str


class HealthResponse(BaseModel):
    """Health check response schema."""

    model_config = ConfigDict(extra="ignore")

    status: str = "healthy"
    service: str = "distribution"
    timestamp: str

"""Request and response schemas for distribution API (P8 §71, Ruling R9, R10)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReleaseSubmitRequest(BaseModel):
    """Compile and submit a release from seam payloads (P8 §11, §71, ADR-0029).

    ``track_manifests`` are C5 payloads, ``media_package`` is the C6 hop-1 payload and
    ``publication`` the optional C6 hop-2 event. The release-level facts are decisions
    the caller makes; none is defaulted. Identity comes from the package's lineage.
    """

    model_config = ConfigDict(extra="forbid")

    track_manifests: list[dict[str, Any]] = Field(min_length=1)
    media_package: dict[str, Any]
    publication: dict[str, Any] | None = None
    artist_name: str = Field(min_length=1)
    release_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    label_name: str = Field(min_length=1)
    copyright_line: str = Field(min_length=1)
    publisher_line: str = Field(min_length=1)
    territories: str = Field(min_length=1)
    release_title: str | None = None
    release_version: int = Field(default=1, ge=1)


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

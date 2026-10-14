"""Pydantic schemas for Phase P6 (Playlist, Visual & Video Factory).

Includes request and response models, plus the frozen Seam C6 contract payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AssemblePackageRequest",
    "HealthResponse",
    "MediaPackageResponse",
    "PlaylistSessionResponse",
    "RenderJobResponse",
]


class AssemblePackageRequest(BaseModel):
    """Assemble a media package from P5's per-track C5 manifests (R7, ADR-0028).

    ``required_duration_seconds``, ``visual_profile`` and ``playlist_concept`` come from
    P3's playlist, visual and title specs and have no defaults. Render profile and
    sequence version are P6's own settings.
    """

    model_config = ConfigDict(extra="forbid")

    track_manifests: list[dict[str, Any]] = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    brand_name: str = Field(min_length=1)
    required_duration_seconds: float = Field(gt=0.0)
    visual_profile: str = Field(min_length=1)
    playlist_concept: str = Field(min_length=1)
    render_profile: str = "yt_longform_1080p_v1"
    sequence_version: str = "v1"


class MediaPackageResponse(BaseModel):
    """Response representing an assembled MediaPackage."""

    model_config = ConfigDict(extra="ignore")

    media_package_id: str
    release_manifest_id: str
    experiment_id: str
    content_arm_id: str
    brand_id: str
    playlist_session_id: str
    visual_session_id: str
    video_master_uri: str
    video_checksum: str
    video_duration_seconds: float
    render_profile: str
    audio_master_uri: str
    audio_checksum: str
    thumbnails: list[dict[str, Any]]
    tracklist: list[dict[str, Any]]
    chapters: list[dict[str, Any]]
    metadata_candidates: list[dict[str, Any]]
    short_source_assets: list[dict[str, Any]]
    render_report: dict[str, Any]
    status: str
    package_version: str


class PlaylistSessionResponse(BaseModel):
    """Response representing a PlaylistSession."""

    model_config = ConfigDict(extra="ignore")

    id: str
    release_manifest_id: str
    experiment_id: str
    content_arm_id: str
    brand_id: str
    status: str
    requested_duration_seconds: float
    actual_duration_seconds: float
    sequence_version: str
    sequence_checksum: str
    tracks: list[dict[str, Any]] = Field(default_factory=list)


class RenderJobResponse(BaseModel):
    """Response representing a RenderJob."""

    model_config = ConfigDict(extra="ignore")

    id: str
    media_package_id: str
    render_profile: str
    render_version: str
    job_status: str
    priority: str
    attempt_count: int
    worker_id: str | None = None
    output_checksum: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


class HealthResponse(BaseModel):
    """Health status response."""

    status: str
    service: str

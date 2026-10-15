"""Pydantic schemas for Phase P6 (Playlist, Visual & Video Factory).

Includes request and response models, plus the frozen Seam C6 contract payload.
"""

from __future__ import annotations

from typing import Any

from plm_contracts.seams.c3 import DurationRangeSeconds
from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import TransitionType

__all__ = [
    "AssemblePackageRequest",
    "HealthResponse",
    "MediaPackageResponse",
    "PlaylistSessionResponse",
    "RenderJobResponse",
]


class AssemblePackageRequest(BaseModel):
    """Assemble a media package from recordings P5 cleared (R7, ADR-0028, R4).

    P6 fetches each candidate's C5 manifest from P5 itself; a caller cannot hand it a
    manifest body. ``duration_range_seconds`` (P3's ``playlist_spec.duration_range_seconds``,
    e.g. 2700–4500 s for ``45_75m``), ``visual_profile`` and ``playlist_concept`` come
    from P3's playlist, visual and title specs and have no defaults; P6 lands the
    timeline inside the range, preferring its midpoint, with no tolerance of its own
    (R5). The brand's name and palette come from P6's brand registry. Render profile and
    transition are P6's own presentation settings.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str] = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    duration_range_seconds: DurationRangeSeconds
    visual_profile: str = Field(min_length=1)
    playlist_concept: str = Field(min_length=1)
    render_profile: str = "yt_longform_1080p_v1"
    transition_type: TransitionType = TransitionType.SHORT_CROSSFADE
    transition_duration_seconds: float = Field(default=2.0, ge=0.0)


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
    blocking_reason_codes: list[str] = Field(default_factory=list)
    """Post-render QA codes that block a ``failed`` package; empty when ready."""


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

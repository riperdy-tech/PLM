"""Content specifications and materialization package models (PHASE-P3 §19–§25, §44)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import PackageStatus, TrackRole


class TrackSpec(BaseModel):
    """The canonical track-level specification for P4 (frozen C3 wire format, Ruling R5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    track_spec_id: str
    playlist_spec_id: str
    experiment_id: str
    content_arm_id: str
    brand_profile_id: str
    hard_constraints: dict[str, Any]
    bounded_preferences: dict[str, Any]
    creative_latitude: dict[str, Any]
    lyric_spec: dict[str, Any]
    vocal_spec: dict[str, Any]
    instrumentation_spec: dict[str, Any]
    production_style: str
    track_role: TrackRole
    requested_duration: int = Field(ge=1)  # in seconds
    candidate_target: int = Field(ge=1)
    priority: int = Field(ge=1)
    budget_limit: float = Field(ge=0.0)
    provider_policy: dict[str, Any] = Field(default_factory=dict)


class PlaylistSpec(BaseModel):
    """Playlist-level sequencing and duration specification for P6 (§19)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    playlist_spec_id: str
    experiment_id: str
    brand_id: str
    track_count_target: int = Field(ge=1)
    total_duration_minutes: int = Field(ge=1)
    sequencing_profile: str = "progressive_arc"


class VisualSpec(BaseModel):
    """Visual theme and motion profile specification for P6 (§22)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    visual_spec_id: str
    experiment_id: str
    visual_world: str
    motion_profile: str
    palette_guide: tuple[str, ...] = ()
    aspect_ratio: str = "16:9"


class ThumbnailSpec(BaseModel):
    """Thumbnail graphic concept and layout specification for P6/P7 (§23)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thumbnail_spec_id: str
    experiment_id: str
    thumbnail_family: str
    composition_archetype: str
    text_rules: dict[str, Any] = Field(default_factory=dict)


class TitleSpec(BaseModel):
    """Title generation template and bounds for P7 (§24)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title_spec_id: str
    experiment_id: str
    title_archetype: str
    formatting_pattern: str
    character_limit: int = Field(default=100, ge=10, le=250)


class ContentPackage(BaseModel):
    """The complete materialized experiment package ready for downstream consumption (§44)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str
    experiment_id: str
    treatment_id: str
    brand_id: str
    status: PackageStatus
    track_specs: tuple[TrackSpec, ...]
    playlist_spec: PlaylistSpec
    visual_spec: VisualSpec
    thumbnail_spec: ThumbnailSpec
    title_spec: TitleSpec
    package_hash: str
    budget_envelope: float = Field(ge=0.0)
    created_at: datetime

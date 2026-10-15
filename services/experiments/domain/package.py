"""Content specifications and materialization package models (PHASE-P3 §19–§25, §44)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from plm_contracts.seams.c3 import DurationRangeSeconds, TrackSpecPayload
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import PackageStatus


class TrackSpec(TrackSpecPayload):
    """P3's track spec: exactly the frozen C3 wire (``plm_contracts.seams.c3``).

    The producer stamps its own schema version; consumers parse with
    :class:`TrackSpecPayload`, which has no default for it.
    """

    schema_version: str = TrackSpecPayload.SCHEMA_VERSION


class PlaylistSpec(BaseModel):
    """Playlist-level sequencing and duration specification for P6 (§19, R5).

    The playlist's length is the declared range ``duration_range_seconds`` (owner
    decision 2026-10-15), which P6 enforces; ``target_duration_seconds`` is only its
    midpoint, for planning. ``track_count_target`` is the supply P3 planned for it:
    ``base_track_count`` to reach the range minimum plus ``spare_track_count`` for P5
    rejections (:mod:`..materialization.supply`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    playlist_spec_id: str
    experiment_id: str
    brand_id: str
    duration_range_seconds: DurationRangeSeconds
    target_duration_seconds: int = Field(ge=1)
    planned_track_duration_seconds: int = Field(ge=1)
    base_track_count: int = Field(ge=1)
    spare_track_count: int = Field(ge=0)
    track_count_target: int = Field(ge=1)
    max_extension_track_count: int = Field(ge=0)
    spare_ratio: Decimal = Field(ge=0)
    supply_policy_version: str = Field(min_length=1)
    sequencing_profile: str = "progressive_arc"

    @model_validator(mode="after")
    def _consistent(self) -> PlaylistSpec:
        if self.track_count_target != self.base_track_count + self.spare_track_count:
            raise ValueError("track_count_target must equal base_track_count + spare_track_count")
        if not self.duration_range_seconds.contains(self.target_duration_seconds):
            raise ValueError("target_duration_seconds must lie inside duration_range_seconds")
        return self


class PackageExtension(BaseModel):
    """One top-up of a package's track specs (``POST .../packages/{id}/extend``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    extension_id: str
    package_id: str
    idempotency_key: str = Field(min_length=1, max_length=255)
    track_count: int = Field(ge=1)
    track_spec_ids: tuple[str, ...]
    created_at: datetime


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
    extensions: tuple[PackageExtension, ...] = ()
    """Top-ups added after materialization; their specs are in ``track_specs`` too."""

    @property
    def extension_track_count(self) -> int:
        return sum(e.track_count for e in self.extensions)

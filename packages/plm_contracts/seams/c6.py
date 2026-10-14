"""Seam C6 · PHASE-P6 → PHASE-P7 → PHASE-P8: publish and distribution identity (R9, ADR-0029).

Hop 1, :class:`MediaPackagePayload`: P6's publish-ready package. P7 publishes it and P8
distributes its tracks. It carries the experiment lineage, the AI-disclosure fact P5
established, and every asset reference with its checksum, so neither consumer invents a
title, URI, checksum, duration or identity.

Hop 2, :class:`YouTubePublicationPayload`: P7's ``youtube_video_published`` event,
carrying the same lineage (R9), so P8 and P9 attribute the video to its arm.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from .base import SeamModel
from .lineage import ExperimentLineage

__all__ = [
    "AudioMaster",
    "ChapterEntry",
    "MediaPackagePayload",
    "MetadataCandidate",
    "ThumbnailAsset",
    "TracklistEntry",
    "VideoMaster",
    "YouTubePublicationPayload",
]

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VideoMaster(_Closed):
    master_uri: NonEmpty
    checksum: Sha256Hex
    duration_seconds: float = Field(gt=0.0)
    render_profile: NonEmpty


class AudioMaster(_Closed):
    master_uri: NonEmpty
    checksum: Sha256Hex


class ThumbnailAsset(_Closed):
    id: NonEmpty
    uri: NonEmpty
    checksum: Sha256Hex
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TracklistEntry(_Closed):
    position: int = Field(ge=1)
    song_id: NonEmpty
    """The P4 candidate id: the recording's identity downstream (ISRC key in P8)."""
    title: NonEmpty
    duration_seconds: float = Field(gt=0.0)
    source_artifact_id: NonEmpty


class ChapterEntry(_Closed):
    title: NonEmpty
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    timestamp_str: NonEmpty
    song_id: NonEmpty


class MetadataCandidate(_Closed):
    variant_id: NonEmpty
    title: NonEmpty
    description: NonEmpty
    tags: tuple[NonEmpty, ...]
    category_id: NonEmpty
    contains_synthetic_media: bool


class MediaPackagePayload(SeamModel):
    """Hop 1: one publish-ready media package and its lineage."""

    SEAM: ClassVar[str] = "C6/hop1"
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    media_package_id: NonEmpty
    release_manifest_id: NonEmpty
    """P6's playlist-level manifest id (R7)."""
    lineage: ExperimentLineage
    brand_id: NonEmpty
    ai_disclosure_required: bool
    """P5's finding for the tracks; P7 sends it as ``containsSyntheticMedia`` (ARCH-P18)."""
    video: VideoMaster
    audio: AudioMaster
    thumbnails: tuple[ThumbnailAsset, ...] = Field(min_length=1)
    tracklist: tuple[TracklistEntry, ...] = Field(min_length=1)
    chapters: tuple[ChapterEntry, ...]
    metadata_candidates: tuple[MetadataCandidate, ...] = Field(min_length=1)
    short_source_assets: tuple[dict[str, Any], ...]
    render_report: dict[str, Any]
    status: NonEmpty


class YouTubePublicationPayload(SeamModel):
    """Hop 2: ``youtube_video_published`` with the full lineage (P7 §65, R9)."""

    SEAM: ClassVar[str] = "C6/hop2"
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    event: Literal["youtube_video_published"]
    youtube_video_id: NonEmpty
    publish_attempt_id: NonEmpty
    channel_id: NonEmpty
    media_package_id: NonEmpty
    lineage: ExperimentLineage
    published_at: AwareDatetime
    metadata_version: NonEmpty
    contains_synthetic_media: bool

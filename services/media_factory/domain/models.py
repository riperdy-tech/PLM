"""Domain models for Phase P6 (Playlist, Visual & Video Factory)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c6 import MediaPackagePayload

from .enums import (
    PackageStatus,
    PlaylistStatus,
    RenderJobStatus,
    TransitionType,
    VisualAssetType,
)
from .ids import (
    media_package_id,
    playlist_session_id,
    playlist_track_id,
    render_job_id,
    thumbnail_variant_id,
    visual_asset_id,
    visual_session_id,
)

__all__ = [
    "Chapter",
    "MediaPackage",
    "PlaylistSession",
    "PlaylistTrack",
    "RenderJob",
    "ThumbnailVariant",
    "VisualAsset",
    "VisualSession",
]


@dataclass(frozen=True)
class PlaylistTrack:
    """One positioned track within a playlist session (P6 §8.2)."""

    id: str
    playlist_session_id: str
    song_id: str
    source_artifact_id: str
    position: int
    start_offset_seconds: float
    end_offset_seconds: float
    transition_type: TransitionType
    transition_duration_seconds: float
    sequence_reason: str
    title: str = ""
    audio_uri: str = ""
    audio_checksum: str = ""
    created_at: datetime = field(kw_only=True)

    @classmethod
    def create(
        cls,
        session_id: str,
        song_id: str,
        source_artifact_id: str,
        position: int,
        start_offset_seconds: float,
        end_offset_seconds: float,
        transition_type: TransitionType = TransitionType.SHORT_CROSSFADE,
        transition_duration_seconds: float = 2.0,
        sequence_reason: str = "unsequenced",
        title: str = "",
        audio_uri: str = "",
        audio_checksum: str = "",
        *,
        now: datetime,
    ) -> PlaylistTrack:
        tid = playlist_track_id(session_id, song_id, position)
        return cls(
            id=tid,
            playlist_session_id=session_id,
            song_id=song_id,
            source_artifact_id=source_artifact_id,
            position=position,
            start_offset_seconds=start_offset_seconds,
            end_offset_seconds=end_offset_seconds,
            transition_type=transition_type,
            transition_duration_seconds=transition_duration_seconds,
            sequence_reason=sequence_reason,
            title=title,
            audio_uri=audio_uri,
            audio_checksum=audio_checksum,
            created_at=now,
        )


@dataclass
class PlaylistSession:
    """A curated listening experience and timeline (P6 §8.1)."""

    id: str
    release_manifest_id: str
    experiment_id: str
    content_arm_id: str
    brand_id: str
    status: PlaylistStatus
    requested_duration_seconds: float
    actual_duration_seconds: float
    sequence_version: str
    sequence_checksum: str
    tracks: list[PlaylistTrack] = field(default_factory=list)
    created_at: datetime = field(kw_only=True)
    updated_at: datetime = field(kw_only=True)

    @classmethod
    def create(
        cls,
        release_manifest_id: str,
        experiment_id: str,
        content_arm_id: str,
        brand_id: str,
        requested_duration_seconds: float,
        sequence_version: str = "v1",
        *,
        now: datetime,
    ) -> PlaylistSession:
        sid = playlist_session_id(release_manifest_id, brand_id, sequence_version)
        return cls(
            id=sid,
            release_manifest_id=release_manifest_id,
            experiment_id=experiment_id,
            content_arm_id=content_arm_id,
            brand_id=brand_id,
            status=PlaylistStatus.PLANNED,
            requested_duration_seconds=requested_duration_seconds,
            actual_duration_seconds=0.0,
            sequence_version=sequence_version,
            sequence_checksum="",
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True)
class VisualAsset:
    """An individual visual element composed into a video (P6 §8.4)."""

    id: str
    visual_session_id: str
    asset_type: VisualAssetType
    source_type: str
    provider: str
    provider_asset_id: str
    uri: str
    checksum: str
    width: int
    height: int
    duration_seconds: float
    fps: float
    license_status: str
    provenance_id: str
    created_at: datetime = field(kw_only=True)

    @classmethod
    def create(
        cls,
        session_id: str,
        asset_type: VisualAssetType,
        uri: str,
        checksum: str,
        width: int = 1920,
        height: int = 1080,
        duration_seconds: float = 0.0,
        fps: float = 30.0,
        source_type: str = "procedural",
        provider: str = "internal",
        provider_asset_id: str = "ast_01",
        license_status: str = "cleared",
        provenance_id: str = "prov_01",
        *,
        now: datetime,
    ) -> VisualAsset:
        aid = visual_asset_id(session_id, asset_type.value, checksum)
        return cls(
            id=aid,
            visual_session_id=session_id,
            asset_type=asset_type,
            source_type=source_type,
            provider=provider,
            provider_asset_id=provider_asset_id,
            uri=uri,
            checksum=checksum,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            fps=fps,
            license_status=license_status,
            provenance_id=provenance_id,
            created_at=now,
        )


@dataclass
class VisualSession:
    """The visual plan and scene narrative for a playlist (P6 §8.3)."""

    id: str
    playlist_session_id: str
    brand_id: str
    brand_version: str
    visual_profile: str
    visual_profile_version: str
    visual_plan_json: dict[str, Any]
    status: str
    assets: list[VisualAsset] = field(default_factory=list)
    created_at: datetime = field(kw_only=True)
    updated_at: datetime = field(kw_only=True)

    @classmethod
    def create(
        cls,
        playlist_session_id_str: str,
        brand_id: str,
        brand_version: str,
        visual_profile: str,
        visual_profile_version: str = "v1",
        visual_plan_json: dict[str, Any] | None = None,
        *,
        now: datetime,
    ) -> VisualSession:
        vsid = visual_session_id(playlist_session_id_str, visual_profile)
        return cls(
            id=vsid,
            playlist_session_id=playlist_session_id_str,
            brand_id=brand_id,
            brand_version=brand_version,
            visual_profile=visual_profile,
            visual_profile_version=visual_profile_version,
            visual_plan_json=visual_plan_json or {},
            status="planned",
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True)
class ThumbnailVariant:
    """A thumbnail candidate for CTR experimentation (P6 §8.5)."""

    id: str
    media_package_id: str
    variant_key: str
    source_assets: list[str]
    layout_version: str
    uri: str
    checksum: str
    width: int
    height: int
    text_payload: str
    predicted_ctr_score: float | None = None
    status: str = "ready"
    created_at: datetime = field(kw_only=True)

    @classmethod
    def create(
        cls,
        media_package_id_str: str,
        variant_key: str,
        source_assets: list[str],
        uri: str,
        checksum: str,
        text_payload: str = "",
        width: int = 1280,
        height: int = 720,
        layout_version: str = "v1",
        predicted_ctr_score: float | None = None,
        *,
        now: datetime,
    ) -> ThumbnailVariant:
        tid = thumbnail_variant_id(media_package_id_str, variant_key)
        return cls(
            id=tid,
            media_package_id=media_package_id_str,
            variant_key=variant_key,
            source_assets=source_assets,
            layout_version=layout_version,
            uri=uri,
            checksum=checksum,
            width=width,
            height=height,
            text_payload=text_payload,
            predicted_ctr_score=predicted_ctr_score,
            created_at=now,
        )


@dataclass(frozen=True)
class Chapter:
    """A chapter marker in the assembled video (P6 §13, §217)."""

    title: str
    start_seconds: float
    end_seconds: float
    song_id: str

    def format_timestamp(self) -> str:
        """Format as MM:SS or HH:MM:SS for YouTube description."""
        secs = int(self.start_seconds)
        mins = secs // 60
        remainder = secs % 60
        if mins >= 60:
            hrs = mins // 60
            mins = mins % 60
            return f"{hrs:02d}:{mins:02d}:{remainder:02d}"
        return f"{mins:02d}:{remainder:02d}"


@dataclass(frozen=True)
class RenderJob:
    """A durable execution unit for audio/video rendering (P6 §8.7, §28)."""

    id: str
    media_package_id: str
    render_profile: str
    render_version: str
    job_status: RenderJobStatus
    priority: str
    attempt_count: int
    worker_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_manifest_hash: str = ""
    output_checksum: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime = field(kw_only=True)

    @classmethod
    def create(
        cls,
        media_package_id_str: str,
        render_profile: str,
        render_version: str = "v1",
        priority: str = "NORMAL",
        input_manifest_hash: str = "",
        attempt: int = 1,
        *,
        now: datetime,
    ) -> RenderJob:
        rjid = render_job_id(media_package_id_str, render_profile, attempt)
        return cls(
            id=rjid,
            media_package_id=media_package_id_str,
            render_profile=render_profile,
            render_version=render_version,
            job_status=RenderJobStatus.QUEUED,
            priority=priority,
            attempt_count=attempt,
            input_manifest_hash=input_manifest_hash,
            created_at=now,
        )


class LegacyPackageError(ValueError):
    """A media package stored before ADR-0028 lacks facts the C6 wire requires."""


@dataclass
class MediaPackage:
    """Downstream contract for P7 and P8 (Seam C6, P6 §5, Ruling R9)."""

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
    status: PackageStatus
    lineage: ExperimentLineage | None = None
    """The tracks' common lineage (ADR-0028); ``None`` only on legacy rows."""
    package_version: str = "v1"
    manifest_uri: str = ""
    manifest_hash: str = ""
    created_at: datetime = field(kw_only=True)
    updated_at: datetime = field(kw_only=True)

    @classmethod
    def create(
        cls,
        release_manifest_id: str,
        experiment_id: str,
        content_arm_id: str,
        brand_id: str,
        playlist_session_id: str,
        visual_session_id: str,
        video_master_uri: str,
        video_checksum: str,
        video_duration_seconds: float,
        render_profile: str,
        audio_master_uri: str,
        audio_checksum: str,
        thumbnails: list[dict[str, Any]],
        tracklist: list[dict[str, Any]],
        chapters: list[dict[str, Any]],
        metadata_candidates: list[dict[str, Any]],
        short_source_assets: list[dict[str, Any]],
        render_report: dict[str, Any],
        lineage: ExperimentLineage,
        package_version: str = "v1",
        status: PackageStatus = PackageStatus.READY_FOR_PUBLISH,
        *,
        now: datetime,
    ) -> MediaPackage:
        mpid = cls.derive_id(release_manifest_id, brand_id, package_version, render_profile)
        content = json.dumps(
            {
                "media_package_id": mpid,
                "release_manifest_id": release_manifest_id,
                "experiment_id": experiment_id,
                "content_arm_id": content_arm_id,
                "lineage": lineage.model_dump(mode="json"),
                "brand_id": brand_id,
                "video_checksum": video_checksum,
                "audio_checksum": audio_checksum,
                "version": package_version,
            },
            sort_keys=True,
        ).encode("utf-8")
        m_hash = hashlib.sha256(content).hexdigest()

        return cls(
            media_package_id=mpid,
            release_manifest_id=release_manifest_id,
            experiment_id=experiment_id,
            content_arm_id=content_arm_id,
            brand_id=brand_id,
            playlist_session_id=playlist_session_id,
            visual_session_id=visual_session_id,
            video_master_uri=video_master_uri,
            video_checksum=video_checksum,
            video_duration_seconds=video_duration_seconds,
            render_profile=render_profile,
            audio_master_uri=audio_master_uri,
            audio_checksum=audio_checksum,
            thumbnails=thumbnails,
            tracklist=tracklist,
            chapters=chapters,
            metadata_candidates=metadata_candidates,
            short_source_assets=short_source_assets,
            render_report=render_report,
            status=status,
            lineage=lineage,
            package_version=package_version,
            manifest_hash=m_hash,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def derive_id(release_manifest_id: str, brand_id: str, package_version: str,
                  render_profile: str) -> str:
        """The package id: one per playlist, brand, sequencing version and render profile
        (a preview and a full render of one playlist are two packages)."""
        return media_package_id(release_manifest_id, brand_id,
                                f"{package_version}|{render_profile}")

    def to_c6_payload(self) -> dict[str, Any]:
        """The Seam C6 hop-1 wire, through the shared model (P6 §5, R9, ADR-0029).

        ``ai_disclosure_required`` is the disclosure the metadata was compiled with, which
        is P5's finding for the tracks. A package stored before lineage existed has no
        complete wire and is refused.
        """
        if self.lineage is None:
            raise LegacyPackageError(
                f"media package {self.media_package_id!r} predates ADR-0028 and has no lineage"
            )
        disclosure = {bool(c["contains_synthetic_media"]) for c in self.metadata_candidates}
        if len(disclosure) != 1:
            raise LegacyPackageError(
                f"media package {self.media_package_id!r} has inconsistent disclosure flags"
            )
        return MediaPackagePayload.build(
            media_package_id=self.media_package_id,
            release_manifest_id=self.release_manifest_id,
            lineage=self.lineage,
            brand_id=self.brand_id,
            ai_disclosure_required=disclosure.pop(),
            video={
                "master_uri": self.video_master_uri,
                "checksum": self.video_checksum,
                "duration_seconds": self.video_duration_seconds,
                "render_profile": self.render_profile,
            },
            audio={"master_uri": self.audio_master_uri, "checksum": self.audio_checksum},
            thumbnails=self.thumbnails,
            tracklist=self.tracklist,
            chapters=self.chapters,
            metadata_candidates=self.metadata_candidates,
            short_source_assets=self.short_source_assets,
            render_report=self.render_report,
            status=self.status.value,
        ).to_wire()

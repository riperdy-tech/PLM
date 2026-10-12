# tests/contract/test_c6_media_to_publishing.py
"""Seam C6 — PHASE-P6 → PHASE-P7 → PHASE-P8 (publish/distribution identity). FROZEN by Ruling R9.

Producer: P6's `MediaPackage` via `to_c6_payload()`.
Consumer: P7's expected publish package schema (P7 §9, Ruling R9).
"""

from __future__ import annotations

from typing import Any

from media_factory.rendering.assembler import assemble_media_package
from media_factory.visuals.planner import BrandProfile
from pydantic import BaseModel, ConfigDict

from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C6 · PHASE-P6 → PHASE-P7 → PHASE-P8"

REGISTRY_FIELDS: tuple[str, ...] = (
    "media_package_id",
    "release_manifest_id",
    "experiment_id",
    "brand_id",
    "video",
    "audio",
    "thumbnails",
    "tracklist",
    "chapters",
    "metadata_candidates",
    "short_source_assets",
    "render_report",
    "status",
)


class VideoPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    master_uri: str
    checksum: str
    duration_seconds: float
    render_profile: str


class AudioPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    master_uri: str
    checksum: str


class ThumbnailItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    uri: str


class ConsumerMediaPackageSchema(BaseModel):
    """P7's expected media package schema for Hop 1, fields copied from registry entry C6."""

    model_config = ConfigDict(extra="forbid")

    media_package_id: str
    release_manifest_id: str
    experiment_id: str
    brand_id: str
    video: VideoPayload
    audio: AudioPayload
    thumbnails: list[ThumbnailItem]
    tracklist: list[dict[str, Any]]
    chapters: list[dict[str, Any]]
    metadata_candidates: list[dict[str, Any]]
    short_source_assets: list[dict[str, Any]]
    render_report: dict[str, Any]
    status: str


def test_declared_fields_match_registry() -> None:
    assert set(ConsumerMediaPackageSchema.model_fields) == set(REGISTRY_FIELDS)


def test_producer_event_validates_against_consumer_schema(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)

    # 1. Assemble media package via P6 pipeline
    manifest = {
        "release_manifest_id": "rm_test_001",
        "experiment_id": "exp_synth_01",
        "content_arm_id": "arm_synth_01",
        "brand_id": "brand_neon",
        "tracks": [
            {
                "song_id": "song_01",
                "title": "Midnight Drive",
                "duration_seconds": 180.0,
                "provenance_status": "PASSED",
                "audio_uri": "media/audio/song_01.wav",
                "audio_checksum": "chk_01",
            },
            {
                "song_id": "song_02",
                "title": "City Lights",
                "duration_seconds": 210.0,
                "provenance_status": "PASSED",
                "audio_uri": "media/audio/song_02.wav",
                "audio_checksum": "chk_02",
            },
        ],
        "playlist_intent": "Late Night Synthwave",
        "policy_flags": {"ai_disclosure_required": True},
    }

    brand = BrandProfile(
        brand_id="brand_neon",
        brand_name="Neon Dreams",
    )

    assembly_result = assemble_media_package(
        release_manifest=manifest,
        brand_profile=brand,
        render_profile="yt_longform_1080p_v1",
        required_duration_seconds=390.0,
    )

    pkg = assembly_result.media_package
    payload = pkg.to_c6_payload()

    # 2. Assert against consumer schema and verify C6 seam
    validated = assert_seam_payload(
        payload,
        ConsumerMediaPackageSchema,
        seam="C6 (Hop 1: P6 → P7)",
        producer="media_factory.rendering.assembler.assemble_media_package",
        consumer="ConsumerMediaPackageSchema (P7 §9)",
    )

    assert isinstance(validated, ConsumerMediaPackageSchema)
    assert validated.media_package_id == pkg.media_package_id
    assert validated.release_manifest_id == "rm_test_001"
    assert validated.experiment_id == "exp_synth_01"
    assert validated.brand_id == "brand_neon"
    assert validated.video.master_uri.endswith(".mp4")
    assert validated.video.render_profile == "yt_longform_1080p_v1"
    assert validated.audio.master_uri.endswith(".wav")
    assert len(validated.thumbnails) >= 1
    assert len(validated.chapters) == 2
    assert validated.chapters[0]["start_seconds"] == 0.0  # YouTube 00:00 requirement
    assert validated.status == "ready_for_publish"


REGISTRY_HOP2_FIELDS: tuple[str, ...] = (
    "event",
    "youtube_video_id",
    "publish_attempt_id",
    "experiment_id",
    "content_arm_id",
    "published_at",
    "metadata_version",
    "experiment_family_id",
    "content_genome_id",
    "production_directive_id",
    "media_package_id",
)


def test_hop2_declared_fields_match_registry() -> None:
    from youtube_publishing.domain.models import YouTubeVideoPublishedEvent

    assert set(YouTubeVideoPublishedEvent.model_fields) == set(REGISTRY_HOP2_FIELDS)


def test_hop2_producer_event_validates_against_consumer_schema(
    contract_registry: SeamRegistry,
) -> None:
    from datetime import UTC, datetime

    from youtube_publishing.domain.models import YouTubeVideoPublishedEvent

    contract_registry.require_entry(REGISTRY_ENTRY)

    now = datetime.now(UTC)
    event = YouTubeVideoPublishedEvent(
        event="youtube_video_published",
        youtube_video_id="dQw4w9WgXcQ",
        publish_attempt_id="att_001_01",
        experiment_id="exp_synth_01",
        content_arm_id="arm_synth_01",
        published_at=now,
        metadata_version="v1",
        experiment_family_id="fam_synth_01",
        content_genome_id="gen_synth_01",
        production_directive_id="dir_synth_01",
        media_package_id="pkg_test_001",
    )

    validated = assert_seam_payload(
        event.model_dump(),
        YouTubeVideoPublishedEvent,
        seam="C6 (Hop 2: P7 → P8/P9)",
        producer="youtube_publishing.engine.YouTubePublishingEngine",
        consumer="P8/P9 distribution & analytics consumer",
    )
    assert isinstance(validated, YouTubeVideoPublishedEvent)
    assert validated.youtube_video_id == "dQw4w9WgXcQ"
    assert validated.experiment_family_id == "fam_synth_01"
    assert validated.content_genome_id == "gen_synth_01"
    assert validated.production_directive_id == "dir_synth_01"
    assert validated.media_package_id == "pkg_test_001"


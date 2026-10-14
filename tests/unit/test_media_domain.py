"""Unit tests for Phase P6 domain models and deterministic identifiers."""

from __future__ import annotations

import pytest
from media_factory.domain.enums import (
    PackageStatus,
)
from media_factory.domain.ids import (
    media_package_id,
    playlist_session_id,
    playlist_track_id,
    render_job_id,
    thumbnail_variant_id,
    visual_asset_id,
    visual_session_id,
)
from media_factory.domain.models import (
    Chapter,
    LegacyPackageError,
    MediaPackage,
)

from tests.support.seams import media_package_fields, sample_lineage


def test_deterministic_identifiers() -> None:
    ps_id1 = playlist_session_id("exp_01", "brand_01", "v1")
    ps_id2 = playlist_session_id("exp_01", "brand_01", "v1")
    assert ps_id1 == ps_id2
    assert ps_id1.startswith("ply_")

    pt_id = playlist_track_id("ps_123", "song_abc", 0)
    assert pt_id.startswith("ptrk_")

    vs_id = visual_session_id("ps_123", "neon_cyber")
    assert vs_id.startswith("vsess_")

    va_id = visual_asset_id("vs_123", "background", "chk_999")
    assert va_id.startswith("vast_")

    tv_id = thumbnail_variant_id("mp_123", "variant_a")
    assert tv_id.startswith("thmb_")

    rj_id = render_job_id("mp_123", "yt_longform_1080p_v1", 1)
    assert rj_id.startswith("rjob_")

    mp_id = media_package_id("exp_01", "brand_01", "v1")
    assert mp_id.startswith("mpkg_")


def test_chapter_formatting() -> None:
    c1 = Chapter(title="Intro", start_seconds=0.0, end_seconds=120.0, song_id="s1")
    assert c1.format_timestamp() == "00:00"

    c2 = Chapter(title="Track 2", start_seconds=245.0, end_seconds=500.0, song_id="s2")
    assert c2.format_timestamp() == "04:05"

    c3 = Chapter(title="Long Track", start_seconds=3665.0, end_seconds=4000.0, song_id="s3")
    assert c3.format_timestamp() == "01:01:05"


def test_media_package_creation_and_c6_payload() -> None:
    wire = media_package_fields("s1")
    pkg = MediaPackage.create(
        release_manifest_id="rm_01",
        experiment_id="exp_test_01",
        content_arm_id="arm_test_01",
        brand_id="brand_neon",
        playlist_session_id="ps_01",
        visual_session_id="vs_01",
        video_master_uri="s3://plm/video.mp4",
        video_checksum=wire["video"]["checksum"],
        video_duration_seconds=3600.0,
        render_profile="yt_longform_1080p_v1",
        audio_master_uri="s3://plm/audio.wav",
        audio_checksum=wire["audio"]["checksum"],
        thumbnails=wire["thumbnails"],
        tracklist=wire["tracklist"],
        chapters=[],
        metadata_candidates=wire["metadata_candidates"],
        short_source_assets=[{"clip_id": "clip_01"}],
        render_report={"status": "passed"},
        lineage=sample_lineage(),
        package_version="v1",
        status=PackageStatus.READY_FOR_PUBLISH,
    )

    assert pkg.media_package_id.startswith("mpkg_")
    assert pkg.manifest_hash != ""

    payload = pkg.to_c6_payload()
    assert payload["media_package_id"] == pkg.media_package_id
    assert payload["lineage"] == sample_lineage().model_dump()
    assert payload["ai_disclosure_required"] is True
    assert payload["video"]["master_uri"] == "s3://plm/video.mp4"
    assert payload["audio"]["master_uri"] == "s3://plm/audio.wav"
    assert payload["status"] == "ready_for_publish"


def test_legacy_package_without_lineage_has_no_c6_wire() -> None:
    pkg = MediaPackage(
        media_package_id="mpkg_legacy", release_manifest_id="rm", experiment_id="e",
        content_arm_id="a", brand_id="b", playlist_session_id="p", visual_session_id="v",
        video_master_uri="u", video_checksum="c", video_duration_seconds=1.0,
        render_profile="r", audio_master_uri="u", audio_checksum="c", thumbnails=[],
        tracklist=[], chapters=[], metadata_candidates=[], short_source_assets=[],
        render_report={}, status=PackageStatus.READY_FOR_PUBLISH,
    )
    with pytest.raises(LegacyPackageError):
        pkg.to_c6_payload()

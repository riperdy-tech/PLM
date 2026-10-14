"""Unit tests for Phase P6 domain models and deterministic identifiers."""

from __future__ import annotations

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
    MediaPackage,
)

from tests.support.seams import sample_lineage


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
    pkg = MediaPackage.create(
        release_manifest_id="rm_01",
        experiment_id="exp_01",
        content_arm_id="arm_01",
        brand_id="brand_neon",
        playlist_session_id="ps_01",
        visual_session_id="vs_01",
        video_master_uri="s3://plm/video.mp4",
        video_checksum="chk_vid_123",
        video_duration_seconds=3600.0,
        render_profile="yt_longform_1080p_v1",
        audio_master_uri="s3://plm/audio.wav",
        audio_checksum="chk_aud_123",
        thumbnails=[{"id": "thumb_a", "uri": "s3://plm/thumb_a.webp"}],
        tracklist=[{"song_id": "s1", "position": 1}],
        chapters=[{"title": "Track 1", "start_seconds": 0.0}],
        metadata_candidates=[{"variant_id": "meta_a", "title": "Great Mix"}],
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
    assert payload["video"]["master_uri"] == "s3://plm/video.mp4"
    assert payload["video"]["checksum"] == "chk_vid_123"
    assert payload["audio"]["master_uri"] == "s3://plm/audio.wav"
    assert payload["audio"]["checksum"] == "chk_aud_123"
    assert payload["status"] == "ready_for_publish"

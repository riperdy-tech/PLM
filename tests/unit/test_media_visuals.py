"""Unit tests for Visual Planner, Artwork Engine, and Metadata Compiler."""

from __future__ import annotations

from datetime import UTC, datetime

from media_factory.artwork.generator import generate_artwork_and_thumbnails
from media_factory.domain.models import PlaylistTrack
from media_factory.metadata.compiler import compile_metadata_and_chapters
from media_factory.visuals.planner import BrandProfile, plan_visual_session

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

def test_visual_planner() -> None:
    brand = BrandProfile(
        brand_id="brand_neon",
        brand_name="Neon Beats",
        palette=["#111111", "#222222", "#333333", "#444444"],
        visual_theme="cyber_lofi",
    )

    res = plan_visual_session(
        playlist_session_id_str="ps_test_01",
        brand_profile=brand,
        visual_profile="neon_cyber_v1",
        total_duration_seconds=180.0,
        scene_length_seconds=60.0,
        now=NOW,
    )

    assert res.visual_session.brand_id == "brand_neon"
    assert res.visual_session.status == "assets_ready"
    assert len(res.assets) == 3
    for a in res.assets:
        assert a.width == 1920
        assert a.height == 1080
        assert a.duration_seconds == 60.0


def test_artwork_and_thumbnail_generator() -> None:
    brand = BrandProfile(
        brand_id="brand_neon",
        brand_name="Neon Beats",
    )

    res = generate_artwork_and_thumbnails(
        media_package_id_str="mp_test_01",
        brand_profile=brand,
        playlist_title="Midnight Synth",
        now=NOW,
    )

    assert res.cover_art_uri.endswith("_cover.webp")
    assert res.cover_art_checksum != ""
    assert len(res.thumbnails) == 3
    for tv in res.thumbnails:
        assert tv.width == 1280
        assert tv.height == 720
        assert tv.checksum != ""


def test_metadata_compiler_starts_at_zero() -> None:
    tracks = [
        PlaylistTrack.create(
            session_id="ps_01",
            song_id="s1",
            source_artifact_id="a1",
            position=0,
            start_offset_seconds=5.0,  # Even if track starts at offset > 0, YouTube chapter 0 must be 00:00
            end_offset_seconds=120.0,
            title="Opening Melody",
            now=NOW,
        ),
        PlaylistTrack.create(
            session_id="ps_01",
            song_id="s2",
            source_artifact_id="a2",
            position=1,
            start_offset_seconds=120.0,
            end_offset_seconds=250.0,
            title="Night Flight",
            now=NOW,
        ),
    ]

    res = compile_metadata_and_chapters(
        tracks=tracks,
        brand_name="Neon Beats",
        playlist_concept="Night Driving",
        ai_disclosure_required=True,
    )

    assert len(res.chapters) == 2
    # YouTube requirement: first chapter starts at 00:00
    assert res.chapters[0]["start_seconds"] == 0.0
    assert res.chapters[0]["timestamp_str"] == "00:00"
    assert res.chapters[1]["start_seconds"] == 120.0
    assert res.chapters[1]["timestamp_str"] == "02:00"

    assert len(res.tracklist) == 2
    assert len(res.metadata_candidates) == 2
    for meta in res.metadata_candidates:
        assert meta["contains_synthetic_media"] is True
        assert "Notice: Sound recording" in meta["description"]

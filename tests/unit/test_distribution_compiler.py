"""Unit tests for release compilation (P8 §11, Ruling R8, R9)."""

from __future__ import annotations

import pytest
from distribution.compiler.release_compiler import (
    ReleaseClearanceError,
    compile_canonical_release_package,
)
from distribution.domain.enums import ReleaseType


def test_compile_single_track_release_success() -> None:
    p5 = {
        "p5_manifest_id": "p5_cleared_001",
        "disposition": "PASSED",
    }
    p6 = {
        "media_package_id": "pkg_media_001",
        "brand_id": "brand_neon",
        "audio_master_uri": "media/audio/song.wav",
        "audio_checksum": "chk_audio_abc",
        "thumbnails": [{"uri": "media/artwork/cover.jpg", "checksum": "chk_cover_abc"}],
        "metadata_candidates": [{"title": "Neon Horizons"}],
    }

    package = compile_canonical_release_package(
        p5_manifest=p5,
        media_package=p6,
        artist_id="art_neon_01",
        artist_name="Neon Collective",
        brand_id="brand_neon",
    )

    assert package.title == "Neon Horizons"
    assert package.release_type == ReleaseType.SINGLE
    assert len(package.tracks) == 1
    assert package.tracks[0].track_number == 1
    assert package.tracks[0].audio_checksum == "chk_audio_abc"
    assert package.artwork_checksum == "chk_cover_abc"
    assert package.source_manifest_ids == ["p5_cleared_001", "pkg_media_001"]
    assert package.submission_fingerprint is not None


def test_compile_multi_track_ep_release() -> None:
    p5 = {
        "p5_manifest_id": "p5_cleared_002",
        "disposition": "PASSED_WITH_WARNINGS",
    }
    p6 = {
        "media_package_id": "pkg_media_002",
        "brand_id": "brand_neon",
        "tracklist": [
            {"title": "Track One", "duration_seconds": 190.0, "audio_uri": "m/1.wav", "audio_checksum": "c1"},
            {"title": "Track Two", "duration_seconds": 210.0, "audio_uri": "m/2.wav", "audio_checksum": "c2"},
            {"title": "Track Three", "duration_seconds": 180.0, "audio_uri": "m/3.wav", "audio_checksum": "c3"},
        ],
        "thumbnails": [{"uri": "media/artwork/ep.jpg", "checksum": "c_ep"}],
    }

    package = compile_canonical_release_package(
        p5_manifest=p5,
        media_package=p6,
        artist_id="art_neon_01",
        artist_name="Neon Collective",
        brand_id="brand_neon",
        release_title="Neon Horizons EP",
    )

    assert package.release_type == ReleaseType.EP
    assert len(package.tracks) == 3
    assert [t.track_number for t in package.tracks] == [1, 2, 3]


def test_compile_fails_when_p5_disposition_not_cleared() -> None:
    p5_quarantined = {
        "p5_manifest_id": "p5_bad_001",
        "disposition": "QUARANTINED",
    }
    p6 = {"media_package_id": "pkg_media_001", "brand_id": "brand_neon"}

    with pytest.raises(ReleaseClearanceError, match="not cleared for distribution"):
        compile_canonical_release_package(
            p5_manifest=p5_quarantined,
            media_package=p6,
            artist_id="art_01",
            artist_name="Artist",
        )

    p5_rejected = {
        "p5_manifest_id": "p5_bad_002",
        "disposition": "REJECTED",
    }
    with pytest.raises(ReleaseClearanceError, match="not cleared for distribution"):
        compile_canonical_release_package(
            p5_manifest=p5_rejected,
            media_package=p6,
            artist_id="art_01",
            artist_name="Artist",
        )


def test_compile_preserves_supplied_isrc() -> None:
    p5 = {"p5_manifest_id": "p5_cleared_003", "disposition": "PASSED"}
    p6 = {
        "media_package_id": "pkg_media_003",
        "brand_id": "brand_neon",
        "tracklist": [
            {
                "title": "Existing Track",
                "isrc": "USPLM2699999",
                "audio_uri": "m/t.wav",
                "audio_checksum": "c_t",
            }
        ],
    }

    package = compile_canonical_release_package(
        p5_manifest=p5,
        media_package=p6,
        artist_id="art_01",
        artist_name="Artist",
    )
    assert package.tracks[0].isrc == "USPLM2699999"

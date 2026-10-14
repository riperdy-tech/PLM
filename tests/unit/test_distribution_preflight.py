"""Unit tests for distribution package preflight validators (P8 §12, §21)."""

from __future__ import annotations

from datetime import UTC, datetime

from distribution.domain.enums import ISRCSource, ReleaseType
from distribution.domain.models import CanonicalReleasePackage, DistributionTrack
from distribution.preflight.validators import validate_canonical_package


def _build_valid_package() -> CanonicalReleasePackage:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    track = DistributionTrack(
        id="rec_trk_01",
        track_id="trk_01",
        release_id="rel_01",
        track_number=1,
        disc_number=1,
        title="Valid Track",
        isrc="USPLM2600001",
        isrc_source=ISRCSource.INTERNAL_ALLOCATION,
        duration_seconds=180.0,
        audio_asset_uri="media/audio/valid.wav",
        audio_checksum="chk_audio_valid",
        explicit=False,
        created_at=now,
    )
    return CanonicalReleasePackage(
        release_id="rel_01",
        release_version=1,
        artist_id="art_01",
        artist_name="Valid Artist",
        brand_id="brand_neon",
        release_type=ReleaseType.SINGLE,
        title="Valid Release",
        release_date="2026-10-15",
        tracks=[track],
        artwork_asset_uri="media/artwork/cover.jpg",
        artwork_checksum="chk_cover_valid",
        source_manifest_ids=["p5_001", "p6_001"],
        submission_fingerprint="fp_valid_123",
        created_at=now,
    )


def test_valid_package_passes_preflight() -> None:
    pkg = _build_valid_package()
    errors = validate_canonical_package(pkg)
    assert errors == []


def test_invalid_package_detects_errors() -> None:
    pkg = _build_valid_package()

    # Empty title
    bad_title_pkg = pkg.model_copy(update={"title": ""})
    errors = validate_canonical_package(bad_title_pkg)
    assert any("title cannot be empty" in e for e in errors)

    # Invalid date
    bad_date_pkg = pkg.model_copy(update={"release_date": "not-a-date"})
    errors = validate_canonical_package(bad_date_pkg)
    assert any("must follow YYYY-MM-DD" in e for e in errors)

    # Missing artwork checksum
    bad_art_pkg = pkg.model_copy(update={"artwork_checksum": ""})
    errors = validate_canonical_package(bad_art_pkg)
    assert any("Artwork checksum is required" in e for e in errors)

    # Empty tracks
    bad_tracks_pkg = pkg.model_copy(update={"tracks": []})
    errors = validate_canonical_package(bad_tracks_pkg)
    assert any("at least one track" in e for e in errors)


def test_track_numbering_gap_detected() -> None:
    pkg = _build_valid_package()
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    t1 = pkg.tracks[0]
    t3 = DistributionTrack(
        id="rec_trk_03",
        track_id="trk_03",
        release_id="rel_01",
        track_number=3,  # Gap: missing track 2
        disc_number=1,
        title="Track Three",
        isrc="USPLM2600003",
        isrc_source=ISRCSource.INTERNAL_ALLOCATION,
        duration_seconds=180.0,
        audio_asset_uri="media/audio/3.wav",
        audio_checksum="chk_3",
        explicit=False,
        created_at=now,
    )
    bad_gap_pkg = pkg.model_copy(update={"tracks": [t1, t3]})
    errors = validate_canonical_package(bad_gap_pkg)
    assert any("sequential from 1 without gaps" in e for e in errors)

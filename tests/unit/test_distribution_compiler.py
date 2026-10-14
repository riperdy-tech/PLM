"""Unit tests for release compilation (P8 §11, Rulings R8, R9, ADR-0029)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from distribution.compiler.release_compiler import (
    ReleaseClearanceError,
    compile_canonical_release_package,
)
from distribution.domain.enums import ISRCSource, ReleaseType
from distribution.identifiers.allocator import allocate_isrc

from tests.support.distribution_db import distribution_db_session
from tests.support.seams import lineage_fields, media_package, release_manifest

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
RELEASE_META: dict[str, Any] = {
    "artist_id": "art_neon_01",
    "artist_name": "Neon Collective",
    "release_date": "2026-10-01",
    "label_name": "Neon Records",
    "copyright_line": "2026 Neon Records",
    "publisher_line": "2026 Neon Music Publishing",
    "territories": "WORLDWIDE",
    "now": NOW,
}


def _counter_allocator() -> Any:
    issued: dict[str, str] = {}

    def allocate(key: str) -> tuple[str, ISRCSource]:
        issued.setdefault(key, f"USPLM26{len(issued) + 1:05d}")
        return issued[key], ISRCSource.INTERNAL_ALLOCATION

    return allocate


def test_compile_single_track_release_from_seam_facts() -> None:
    manifest = release_manifest(candidate_id="cand_01", measured_duration_seconds=187.5)
    pkg = media_package("cand_01")
    package = compile_canonical_release_package(
        track_manifests=[manifest], media_package=pkg, allocate_isrc=_counter_allocator(),
        **RELEASE_META,
    )
    assert package.title == pkg.metadata_candidates[0].title
    assert package.release_type == ReleaseType.SINGLE
    track = package.tracks[0]
    assert track.duration_seconds == 187.5  # P5's measured duration, not a default
    assert track.audio_asset_uri == manifest.artifact_uri
    assert track.audio_checksum == manifest.artifact_sha256
    assert package.artwork_checksum == pkg.thumbnails[0].checksum
    assert package.source_manifest_ids == [manifest.p5_manifest_id, pkg.media_package_id]


def test_compile_multi_track_ep_release() -> None:
    songs = ("cand_01", "cand_02", "cand_03")
    package = compile_canonical_release_package(
        track_manifests=[release_manifest(candidate_id=c) for c in songs],
        media_package=media_package(*songs),
        allocate_isrc=_counter_allocator(),
        release_title="Neon Horizons EP",
        **RELEASE_META,
    )
    assert package.release_type == ReleaseType.EP
    assert [t.track_number for t in package.tracks] == [1, 2, 3]
    assert len({t.isrc for t in package.tracks}) == 3


@pytest.mark.parametrize("disposition", ["QUARANTINED", "REJECTED"])
def test_compile_refuses_an_uncleared_track(disposition: str) -> None:
    with pytest.raises(ReleaseClearanceError) as exc:
        compile_canonical_release_package(
            track_manifests=[release_manifest(candidate_id="cand_01", disposition=disposition)],
            media_package=media_package("cand_01"),
            allocate_isrc=_counter_allocator(),
            **RELEASE_META,
        )
    assert exc.value.error.code == "track_not_cleared"


def test_compile_refuses_a_track_from_another_lineage() -> None:
    with pytest.raises(ReleaseClearanceError) as exc:
        compile_canonical_release_package(
            track_manifests=[
                release_manifest(
                    candidate_id="cand_01", lineage=lineage_fields(content_arm_id="arm_other")
                )
            ],
            media_package=media_package("cand_01"),
            allocate_isrc=_counter_allocator(),
            **RELEASE_META,
        )
    assert exc.value.error.code == "lineage_mismatch"


def test_compile_refuses_a_tracklist_that_names_other_recordings() -> None:
    with pytest.raises(ReleaseClearanceError) as exc:
        compile_canonical_release_package(
            track_manifests=[release_manifest(candidate_id="cand_01")],
            media_package=media_package("cand_99"),
            allocate_isrc=_counter_allocator(),
            **RELEASE_META,
        )
    assert exc.value.error.code == "tracklist_mismatch"


def test_isrc_is_stable_per_recording_and_sequential_across_recordings() -> None:
    with distribution_db_session() as session:
        first, _ = allocate_isrc(session, "cand_01", now=NOW)
        again, _ = allocate_isrc(session, "cand_01", now=NOW)
        second, _ = allocate_isrc(session, "cand_02", now=NOW)
    assert first == again  # never regenerated for the same recording
    assert first == "USPLM2600001"
    assert second == "USPLM2600002"


def test_two_releases_no_longer_collide_on_isrc() -> None:
    # Before ADR-0029 every release's first track got USPLM2600001 (from its position).
    with distribution_db_session() as session:
        alloc = lambda key: allocate_isrc(session, key, now=NOW)
        a = compile_canonical_release_package(
            track_manifests=[release_manifest(candidate_id="cand_a")],
            media_package=media_package("cand_a"), allocate_isrc=alloc, **RELEASE_META,
        )
        b = compile_canonical_release_package(
            track_manifests=[release_manifest(candidate_id="cand_b")],
            media_package=media_package("cand_b"), allocate_isrc=alloc, **RELEASE_META,
        )
    assert a.tracks[0].isrc != b.tracks[0].isrc

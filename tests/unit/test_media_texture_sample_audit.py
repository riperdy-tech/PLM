"""Bounded, read-only P6 cross-release audio texture sample audit."""
from __future__ import annotations

import pytest
from media_factory.storage.models import media_packages_table
from media_factory.texture_sample_audit import (
    MeasuredSample,
    compare_cross_release_samples,
    load_sample_sources,
    measure_sources,
    sample_positions,
)
from sqlalchemy import update

from tests.support.media_backend import InMemoryArtifactStore
from tests.support.media_db import media_db_session
from tests.unit.test_media_texture_backfill import _legacy


def _signature(bin_index: int = 0) -> tuple[float, ...]:
    values = [0.0] * 192
    for window in range(4):
        values[window * 48 + bin_index] = 1.0
    return tuple(values)


def _sample(
    pkg: str, release: str, song: str, *, bin_index: int = 0, sha: str = "a" * 64
) -> MeasuredSample:
    return MeasuredSample(pkg, release, song, sha, _signature(bin_index))


@pytest.mark.parametrize(
    ("tracks", "count", "wanted"),
    [
        (19, 1, (0,)),
        (19, 2, (0, 18)),
        (23, 3, (0, 11, 22)),
        (3, 3, (0, 1, 2)),
    ],
)
def test_sample_positions_cover_nonoverlapping_album_areas(
    tracks: int, count: int, wanted: tuple[int, ...],
) -> None:
    assert sample_positions(tracks, count) == wanted


@pytest.mark.parametrize(("tracks", "requested"), [(1, 2), (0, 1), (10, 0), (10, 4)])
def test_sample_positions_refuse_unsupported_counts(tracks: int, requested: int) -> None:
    with pytest.raises(ValueError):
        sample_positions(tracks, requested)


def test_sample_comparison_counts_only_cross_release_pairs() -> None:
    samples = (
        _sample("p1", "r1", "a", sha="a" * 64),
        _sample("p1", "r1", "b", bin_index=1, sha="b" * 64),
        _sample("p2", "r2", "c", sha="c" * 64),
        _sample("p2", "r2", "d", bin_index=1, sha="d" * 64),
    )
    result = compare_cross_release_samples(samples)
    assert result["compared_pairs"] == 4
    assert result["max_similarity"] == pytest.approx(1)
    assert result["at_or_above_near_copy_threshold"] == 2
    assert result["exact_sha256_reuse_pairs"] == 0
    assert len(result["top_pairs"]) == 4
    assert result["scope"] == "read_only_selected_track_samples_not_full_album_or_rights_clearance"


def test_sample_comparison_reports_exact_checksum_reuse_even_with_different_ids() -> None:
    report = compare_cross_release_samples([
        _sample("p1", "r1", "a"),
        _sample("p2", "r2", "other"),
    ])
    assert report["exact_sha256_reuse_pairs"] == 1
    assert report["at_or_above_near_copy_threshold"] == 1


def test_sample_comparison_refuses_unmeasurable_and_single_release() -> None:
    with pytest.raises(ValueError, match="no cross-release"):
        compare_cross_release_samples([
            _sample("p1", "r1", "a"), _sample("p2", "r1", "b"),
        ])
    with pytest.raises(ValueError, match="invalid"):
        compare_cross_release_samples([
            _sample("p1", "r1", "a"),
            MeasuredSample("p2", "r2", "b", "a" * 64, (0.0,) * 192),
        ])


def test_sample_sources_validate_full_playlist_without_changing_db() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        first = _legacy(session, store, suffix="_sample_a")
        second = _legacy(session, store, suffix="_sample_b")
        sources, total = load_sample_sources(
            session, (first.media_package_id, second.media_package_id),
            samples_per_album=2,
        )
        assert total == 6
        assert len(sources) == 4
        assert [s.song_id for s in sources] == [
            "song_0_sample_a", "song_2_sample_a",
            "song_0_sample_b", "song_2_sample_b",
        ]
        results = measure_sources(store, sources)
        assert len(results) == 4
        report = compare_cross_release_samples(results)
        assert report["sampled_tracks"] == 4
        assert report["compared_pairs"] == 4
        assert report["threshold"] == 0.998


def test_full_audio_source_selection_and_all_cross_release_pairs() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        first = _legacy(session, store, suffix="_full_a")
        second = _legacy(session, store, suffix="_full_b")
        sources, count = load_sample_sources(
            session, (first.media_package_id, second.media_package_id),
            full_album=True,
        )
        assert count == len(sources) == 6
        measured = measure_sources(store, sources)
        report = compare_cross_release_samples(measured, full_album=True)
        assert report["compared_pairs"] == 9
        assert "full_source" in str(report["scope"])


def test_full_audio_comparison_refuses_more_than_200_tracks() -> None:
    samples = [_sample("p1", "r1", f"a{i}") for i in range(101)]
    samples.extend(_sample("p2", "r2", f"b{i}") for i in range(100))
    with pytest.raises(ValueError, match="2..200"):
        compare_cross_release_samples(samples, full_album=True)


def test_full_audio_comparison_refuses_duplicate_source_rows() -> None:
    with pytest.raises(ValueError, match="repeated"):
        compare_cross_release_samples([
            _sample("p1", "r1", "a"),
            _sample("p1", "r1", "a"),
            _sample("p2", "r2", "b"),
        ], full_album=True)


def test_sample_source_loading_rejects_playlist_identity_drift() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        first = _legacy(session, store, suffix="_drift_a")
        second = _legacy(session, store, suffix="_drift_b")
        session.execute(
            update(media_packages_table)
            .where(media_packages_table.c.media_package_id == first.media_package_id)
            .values(brand_id="wrong_brand")
        )
        with pytest.raises(ValueError, match="source playlist identity mismatch"):
            load_sample_sources(session, (first.media_package_id, second.media_package_id))


def test_sample_source_loading_rejects_duplicate_packages() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        first = _legacy(session, store, suffix="_dup")
        with pytest.raises(ValueError, match="2..8 distinct"):
            load_sample_sources(session, (first.media_package_id, first.media_package_id))

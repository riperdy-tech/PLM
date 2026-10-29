"""Cross-album same-brand acoustic evidence, repository and render-preflight tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from media_factory.audio.features import DEFAULT_FEATURE_POLICY
from media_factory.domain.enums import PackageStatus, PlaylistStatus
from media_factory.domain.models import MediaPackage
from media_factory.errors import MediaAssemblyError
from media_factory.playlist.catalog import (
    CatalogAlbum,
    check_catalog_texture,
    serialize_album_signatures,
)
from media_factory.playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY
from media_factory.storage.models import playlist_sessions_table, playlist_tracks_table
from media_factory.storage.repositories import insert_media_package, list_catalog_albums

from tests.support.media_audio import synth_track
from tests.support.media_db import media_db_session
from tests.support.seams import sample_lineage

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def _record(package_id: str, release_id: str, evidence) -> CatalogAlbum:
    return CatalogAlbum(
        package_id, release_id, evidence,
        policy_version=DEFAULT_ALBUM_TEXTURE_POLICY.version,
        extractor_version=DEFAULT_FEATURE_POLICY.version,
    )


def _measured_pair():
    from media_factory.audio.features import measure_features

    a = measure_features("a", synth_track(seconds=4, bpm=120, tonic=0))
    b = measure_features("b", synth_track(seconds=4, bpm=120, tonic=0))
    return {"a": a, "b": b}


def test_reused_recording_across_different_release_is_blocked_before_render() -> None:
    measured = _measured_pair()
    sha = {"a": "a" * 64, "b": "b" * 64}
    evidence = serialize_album_signatures(["a"], measured, sha)
    history = [_record("pkg_previous", "rel_previous", evidence["track_signatures"])]
    with pytest.raises(MediaAssemblyError) as exc:
        check_catalog_texture(
            ["b"], measured, sha, current_release_manifest_id="rel_new",
            history=history,
        )
    assert exc.value.code == "album_catalog_near_duplicate"
    assert "pkg_previous" in str(exc.value)


def test_exact_checksum_reuse_is_always_refused() -> None:
    measured = _measured_pair()
    sha = {"a": "1" * 64, "b": "1" * 64}
    evidence = serialize_album_signatures(["a"], measured, sha)
    with pytest.raises(MediaAssemblyError, match="pkg_old"):
        check_catalog_texture(
            ["b"], measured, sha, current_release_manifest_id="rel_next",
            history=[_record("pkg_old", "rel_old", evidence["track_signatures"])],
        )


@pytest.mark.parametrize(
    ("policy_version", "extractor_version"),
    [
        (None, DEFAULT_FEATURE_POLICY.version),
        (DEFAULT_ALBUM_TEXTURE_POLICY.version, None),
        ("album-texture-v999", DEFAULT_FEATURE_POLICY.version),
        (DEFAULT_ALBUM_TEXTURE_POLICY.version, "p6-features-v0"),
    ],
)
def test_incompatible_legacy_versions_never_pass_catalog_gate(
    policy_version: str | None, extractor_version: str | None,
) -> None:
    measured = _measured_pair()
    evidence = serialize_album_signatures(["a"], measured, {"a": "a" * 64})
    historical = CatalogAlbum(
        "legacy", "old_release", evidence["track_signatures"],
        policy_version=policy_version, extractor_version=extractor_version,
    )
    with pytest.raises(MediaAssemblyError) as exc:
        check_catalog_texture(
            ["b"], measured, {"b": "b" * 64},
            current_release_manifest_id="new_release", history=[historical],
        )
    assert exc.value.code == "album_catalog_version_incompatible"


def test_same_release_rerender_exempt_but_provenance_not_fabricated() -> None:
    measured = _measured_pair()
    sha = {"a": "a" * 64, "b": "b" * 64}
    result = check_catalog_texture(
        ["b"], measured, sha, current_release_manifest_id="rel_current",
        history=[_record("pkg_old_profile", "rel_current", None)],
    )
    assert result["compared_packages"] == 0
    assert result["max_similarity"] is None


def test_unmeasured_historical_package_holds_instead_of_false_safe() -> None:
    measured = _measured_pair()
    with pytest.raises(MediaAssemblyError) as exc:
        check_catalog_texture(
            ["b"], measured, {"b": "b" * 64},
            current_release_manifest_id="new",
            history=[_record("legacy_pkg", "legacy", None)],
        )
    assert exc.value.code == "album_catalog_unmeasurable"


def test_corrupt_or_incomplete_historical_evidence_is_refused() -> None:
    measured = _measured_pair()
    for values in ([], [{"candidate_id": "a", "artifact_sha256": "x", "signature": []}]):
        with pytest.raises(MediaAssemblyError, match="album"):
            check_catalog_texture(
                ["b"], measured, {"b": "b" * 64},
                current_release_manifest_id="new",
                history=[_record("old_album", "old", values)],
            )


@pytest.mark.parametrize("signature", [
    [0.0] * 192,  # False low similarity from zero vectors.
    [1.0] * 192,  # False high similarity from non-unit vectors.
    [-0.1] * 192,  # Negative magnitudes cannot come from the extractor.
])
def test_invalid_historical_spectral_norms_hold_catalog(signature: list[float]) -> None:
    measured = _measured_pair()
    history = [_record("corrupt_album", "rel_old", [{
        "candidate_id": "a", "artifact_sha256": "a" * 64, "signature": signature,
    }])]
    with pytest.raises(MediaAssemblyError) as exc:
        check_catalog_texture(
            ["b"], measured, {"b": "b" * 64},
            current_release_manifest_id="rel_new", history=history,
        )
    assert exc.value.code == "album_catalog_unmeasurable"


def test_non_hex_saved_sha256_is_not_accepted_as_evidence() -> None:
    measured = _measured_pair()
    history = [_record("corrupt_album", "rel_old", [{
        "candidate_id": "a", "artifact_sha256": "z" * 64,
        "signature": list(measured["a"].texture_signature or ()),
    }])]
    with pytest.raises(MediaAssemblyError) as exc:
        check_catalog_texture(
            ["b"], measured, {"b": "b" * 64},
            current_release_manifest_id="rel_new", history=history,
        )
    assert exc.value.code == "album_catalog_unmeasurable"


def _sample_package(release_id: str, brand: str, *, report: dict, tracks: list[dict]):
    return MediaPackage.create(
        release_manifest_id=release_id,
        experiment_id="exp",
        content_arm_id="arm",
        brand_id=brand,
        playlist_session_id="session_" + release_id,
        visual_session_id="visual_" + release_id,
        video_master_uri="s3://video",
        video_checksum="c",
        video_duration_seconds=180,
        render_profile="preview_360p_v1",
        audio_master_uri="s3://audio",
        audio_checksum="a",
        thumbnails=[],
        tracklist=[
            {
                "position": index,
                "source_artifact_id": f"source_{track['song_id']}",
                **track,
            }
            for index, track in enumerate(tracks, 1)
        ],
        chapters=[],
        metadata_candidates=[],
        short_source_assets=[],
        render_report=report,
        lineage=sample_lineage(),
        status=PackageStatus.READY_FOR_PUBLISH,
        now=NOW,
    )


def _seed_archived_source_records(
    session, package: MediaPackage, *, checksum: str = "a" * 64,
    source_id: str | None = None,
) -> None:
    """Create original playlist metadata for catalog tests, never audio files."""
    session.execute(playlist_sessions_table.insert().values(
        id=package.playlist_session_id,
        release_manifest_id=package.release_manifest_id,
        experiment_id=package.experiment_id,
        content_arm_id=package.content_arm_id,
        brand_id=package.brand_id,
        status=PlaylistStatus.RENDERED.value,
        requested_duration_seconds=180.0,
        actual_duration_seconds=4.0,
        sequence_version="v1",
        sequence_checksum="",
        created_at=NOW,
        updated_at=NOW,
    ))
    for i, item in enumerate(package.tracklist):
        session.execute(playlist_tracks_table.insert().values(
            id=f"source_{package.playlist_session_id}_{i}",
            playlist_session_id=package.playlist_session_id,
            song_id=item["song_id"],
            source_artifact_id=(
                source_id if source_id is not None else item["source_artifact_id"]
            ),
            position=i,
            start_offset_seconds=float(i) * 4.0,
            end_offset_seconds=float(i + 1) * 4.0,
            transition_type="short_crossfade",
            transition_duration_seconds=2.0,
            sequence_reason="test",
            title=f"Track {i+1}",
            audio_uri=f"memory://original_{i}",
            audio_checksum=checksum,
            created_at=NOW,
        ))


def test_db_catalog_is_same_brand_only_and_legacy_is_visible() -> None:
    with media_db_session() as session:
        signature = _measured_pair()["a"].texture_signature
        assert signature is not None
        valid = _sample_package(
            "rel_a", "brand_a",
            report={"album_texture": {"evidence": {
                "policy_version": DEFAULT_ALBUM_TEXTURE_POLICY.version,
                "extractor_version": DEFAULT_FEATURE_POLICY.version,
                "track_signatures": [
                    {"candidate_id": "a", "artifact_sha256": "a" * 64,
                     "signature": list(signature)},
                ],
            }}},
            tracks=[{"song_id": "a"}],
        )
        insert_media_package(session, valid)
        _seed_archived_source_records(session, valid)
        insert_media_package(session, _sample_package(
            "rel_b", "brand_b", report={"old": True}, tracks=[{"song_id": "b"}],
        ))
        own = list_catalog_albums(session, "brand_a")
        assert len(own) == 1
        assert own[0].track_signatures is not None
        assert own[0].release_manifest_id == "rel_a"
        assert own[0].policy_version == DEFAULT_ALBUM_TEXTURE_POLICY.version
        assert own[0].extractor_version == DEFAULT_FEATURE_POLICY.version
        other = list_catalog_albums(session, "brand_b")
        assert len(other) == 1
        assert other[0].track_signatures is None
        with pytest.raises(ValueError, match="positive max_packages"):
            list_catalog_albums(session, "brand_a", max_packages=0)
        assert len(list_catalog_albums(session, "brand_a", max_packages=1)) == 1


@pytest.mark.parametrize("damage", [
    "missing_header", "checksum", "source_artifact_id", "audio_uri",
    "playlist_arm", "missing_track",
])
def test_archived_source_metadata_must_match_before_catalog_comparison(
    damage: str,
) -> None:
    measured = _measured_pair()
    evidence = serialize_album_signatures(["a"], measured, {"a": "a" * 64})
    package = _sample_package(
        "rel_original", "brand_archive",
        report={"album_texture": {"evidence": evidence}},
        tracks=[{"song_id": "a"}],
    )
    with media_db_session() as session:
        insert_media_package(session, package)
        if damage != "missing_header":
            _seed_archived_source_records(session, package)
        if damage == "checksum":
            session.execute(playlist_tracks_table.update().values(audio_checksum="f" * 64))
        elif damage == "source_artifact_id":
            session.execute(playlist_tracks_table.update().values(source_artifact_id="wrong"))
        elif damage == "audio_uri":
            session.execute(playlist_tracks_table.update().values(audio_uri=""))
        elif damage == "playlist_arm":
            session.execute(
                playlist_sessions_table.update().values(content_arm_id="wrong")
            )
        elif damage == "missing_track":
            session.execute(playlist_tracks_table.delete())

        history = list_catalog_albums(session, "brand_archive")
        assert len(history) == 1
        assert history[0].track_signatures is None
        with pytest.raises(MediaAssemblyError) as exc:
            check_catalog_texture(
                ["b"], measured, {"b": "b" * 64},
                current_release_manifest_id="rel_new",
                history=history,
            )
        assert exc.value.code == "album_catalog_unmeasurable"


def test_repo_does_not_silently_truncate_brand_history() -> None:
    with media_db_session() as session:
        for i in range(2):
            insert_media_package(
                session, _sample_package(
                    f"rel_{i}", "brand_a",
                    report={"album_texture": {"evidence": {
                        "policy_version": DEFAULT_ALBUM_TEXTURE_POLICY.version,
                        "extractor_version": DEFAULT_FEATURE_POLICY.version,
                        "track_signatures": [
                        {"candidate_id": f"song_{i}", "artifact_sha256": "a" * 64,
                         "signature": [0.1] * 192},
                    ]}}},
                    tracks=[{"song_id": f"song_{i}"}],
                ),
            )
        with pytest.raises(MediaAssemblyError) as exc:
            list_catalog_albums(session, "brand_a", max_packages=1)
        assert exc.value.code == "album_catalog_scope_incomplete"


def test_repo_refuses_mismatched_signature_identity_in_past_album() -> None:
    with media_db_session() as session:
        insert_media_package(session, _sample_package(
            "release_legacy", "brand_a",
            report={"album_texture": {"evidence": {
                "policy_version": DEFAULT_ALBUM_TEXTURE_POLICY.version,
                "extractor_version": DEFAULT_FEATURE_POLICY.version,
                "track_signatures": [
                {"candidate_id": "unrelated", "artifact_sha256": "a" * 64,
                 "signature": [0.1] * 192},
            ]}}},
            tracks=[{"song_id": "original"}],
        ))
        history = list_catalog_albums(session, "brand_a")
        assert len(history) == 1 and history[0].track_signatures is None


def test_repo_requires_historical_signature_order_not_just_matching_id_set() -> None:
    entries = [
        {"candidate_id": song, "artifact_sha256": "a" * 64, "signature": [0.1] * 192}
        for song in ("song_a", "song_b")
    ]
    with media_db_session() as session:
        insert_media_package(session, _sample_package(
            "ordered_reference", "brand_order",
            report={"album_texture": {"evidence": {
                "policy_version": DEFAULT_ALBUM_TEXTURE_POLICY.version,
                "extractor_version": DEFAULT_FEATURE_POLICY.version,
                "track_signatures": list(reversed(entries)),
            }}},
            tracks=[{"song_id": "song_a"}, {"song_id": "song_b"}],
        ))
        history = list_catalog_albums(session, "brand_order")
        assert len(history) == 1
        assert history[0].track_signatures is None
        with pytest.raises(MediaAssemblyError) as exc:
            check_catalog_texture(
                ["new"], {"new": _measured_pair()["a"]},
                {"new": "b" * 64},
                current_release_manifest_id="different",
                history=history,
            )
        assert exc.value.code == "album_catalog_unmeasurable"


@pytest.mark.parametrize("invalid_track", [
    {"position": 0},
    {"position": 2},
    {"position": True},
    {"source_artifact_id": ""},
    {"song_id": ""},
])
def test_repo_refuses_corrupt_saved_track_identity(invalid_track: dict) -> None:
    with media_db_session() as session:
        insert_media_package(session, _sample_package(
            "corrupt_track", "brand_corrupt",
            report={"album_texture": {"evidence": {
                "policy_version": DEFAULT_ALBUM_TEXTURE_POLICY.version,
                "extractor_version": DEFAULT_FEATURE_POLICY.version,
                "track_signatures": [
                    {"candidate_id": "a", "artifact_sha256": "a" * 64,
                     "signature": [0.1] * 192},
                ],
            }}},
            tracks=[{"song_id": "a", **invalid_track}],
        ))
        history = list_catalog_albums(session, "brand_corrupt")
        assert len(history) == 1 and history[0].track_signatures is None


def test_repo_rejects_repeated_song_id_in_historical_tracklist() -> None:
    with media_db_session() as session:
        insert_media_package(session, _sample_package(
            "duplicate_songs", "brand_duplicates",
            report={"album_texture": {"evidence": {
                "policy_version": DEFAULT_ALBUM_TEXTURE_POLICY.version,
                "extractor_version": DEFAULT_FEATURE_POLICY.version,
                "track_signatures": [
                    {"candidate_id": "a", "artifact_sha256": "a" * 64,
                     "signature": [0.1] * 192},
                    {"candidate_id": "a", "artifact_sha256": "a" * 64,
                     "signature": [0.1] * 192},
                ],
            }}},
            tracks=[{"song_id": "a"}, {"song_id": "a"}],
        ))
        history = list_catalog_albums(session, "brand_duplicates")
        assert len(history) == 1 and history[0].track_signatures is None


def test_repo_treats_unexpected_render_report_shape_as_unmeasurable() -> None:
    with media_db_session() as session:
        insert_media_package(session, _sample_package(
            "invalid_report_type", "brand_invalid",
            report=[], tracks=[{"song_id": "song_a"}],
        ))
        history = list_catalog_albums(session, "brand_invalid")
        assert len(history) == 1 and history[0].track_signatures is None

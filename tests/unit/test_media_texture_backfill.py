"""P6 legacy album backfill: measured evidence, dry-run, atomic update and refusal."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from media_factory.audio.features import DEFAULT_FEATURE_POLICY
from media_factory.domain.enums import PackageStatus
from media_factory.domain.models import MediaPackage, PlaylistSession, PlaylistTrack
from media_factory.errors import MediaAssemblyError
from media_factory.playlist.catalog import CatalogAlbum, validate_catalog_album
from media_factory.playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY
from media_factory.storage.models import (
    media_packages_table,
    playlist_sessions_table,
    playlist_tracks_table,
)
from media_factory.storage.repositories import (
    get_media_package_by_id,
    insert_media_package,
    insert_playlist_session,
    insert_playlist_track,
    list_catalog_albums,
)
from media_factory.texture_backfill import backfill_package_texture, backfill_packages_texture

from tests.support.media_audio import synth_track
from tests.support.media_backend import InMemoryArtifactStore, synth_specs
from tests.support.media_db import create_media_engine, media_db_session, media_session
from tests.support.seams import sample_lineage

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def _legacy(
    session, store: InMemoryArtifactStore, *,
    corrupt_track: bool = False, suffix: str = "",
):
    playlist = PlaylistSession.create(
        "release_legacy" + suffix, "exp_legacy" + suffix,
        "arm_legacy" + suffix, "brand_neon", 180, now=NOW,
    )
    insert_playlist_session(session, playlist)
    tracklist = []
    for i, spec in enumerate(synth_specs()):
        artifact = store.put_source(f"song_{i}{suffix}", synth_track(seconds=4, **spec))
        row = PlaylistTrack.create(
            playlist.id, f"song_{i}{suffix}", f"artifact_{i}{suffix}", i,
            i * 60.0, (i + 1) * 60.0, title=f"Track {i+1}",
            audio_uri=artifact.uri, audio_checksum=artifact.sha256, now=NOW,
        )
        insert_playlist_track(session, row)
        tracklist.append({
            "position": i + 1,
            "song_id": row.song_id,
            "title": row.title,
            "source_artifact_id": row.source_artifact_id,
        })
    if corrupt_track:
        tracklist[1]["source_artifact_id"] = "unrelated"
    pkg = MediaPackage.create(
        release_manifest_id="release_legacy" + suffix,
        experiment_id="exp_legacy" + suffix,
        content_arm_id="arm_legacy" + suffix,
        brand_id="brand_neon",
        playlist_session_id=playlist.id,
        visual_session_id="visual_legacy",
        video_master_uri="media://video",
        video_checksum="v",
        video_duration_seconds=180.0,
        render_profile="preview_360p_v1",
        audio_master_uri="media://audio",
        audio_checksum="a",
        thumbnails=[],
        tracklist=tracklist,
        chapters=[],
        metadata_candidates=[],
        short_source_assets=[],
        render_report={"qa": {"status": "passed"}, "album_texture": {"scope": "legacy"}},
        lineage=sample_lineage(),
        now=NOW,
    )
    insert_media_package(session, pkg)
    return pkg


def test_dry_run_proves_audio_without_changing_stored_report() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        before = get_media_package_by_id(session, pkg.media_package_id)
        assert before is not None
        result = backfill_package_texture(
            session, store, pkg.media_package_id, apply=False, now=NOW,
        )
        assert result.status == "validated_dry_run" and not result.applied
        after = get_media_package_by_id(session, pkg.media_package_id)
        assert after is not None
        assert after.render_report == before.render_report
        assert after.manifest_hash == before.manifest_hash


def test_apply_is_idempotent_and_preserves_qa_and_c6_facts() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        before = get_media_package_by_id(session, pkg.media_package_id)
        assert before is not None
        result = backfill_package_texture(session, store, pkg.media_package_id,
                                          apply=True, now=NOW)
        assert result.status == "applied" and result.applied
        after = get_media_package_by_id(session, pkg.media_package_id)
        assert after is not None
        assert after.render_report["qa"] == before.render_report["qa"]
        assert after.manifest_hash == before.manifest_hash
        assert after.chapters == before.chapters
        texture = after.render_report["album_texture"]
        assert texture["scope"] == "within_album_near_copy_only"
        assert texture["backfill"]["measured_from_sha256_verified_sources"] is True
        assert len(texture["evidence"]["track_signatures"]) == 3
        assert texture["evidence"]["policy_version"] == DEFAULT_ALBUM_TEXTURE_POLICY.version
        assert texture["evidence"]["extractor_version"] == DEFAULT_FEATURE_POLICY.version
        validate_catalog_album(
            CatalogAlbum(
                pkg.media_package_id, pkg.release_manifest_id,
                texture["evidence"]["track_signatures"],
                policy_version=texture["evidence"]["policy_version"],
                extractor_version=texture["evidence"]["extractor_version"],
            ),
            policy=DEFAULT_ALBUM_TEXTURE_POLICY,
        )
        assert list_catalog_albums(session, "brand_neon")[0].track_signatures is not None
        again = backfill_package_texture(
            session, store, pkg.media_package_id, apply=True, now=NOW,
        )
        assert again.status == "already_present" and not again.applied
        assert get_media_package_by_id(session, pkg.media_package_id).render_report == after.render_report


def test_corrupt_source_bytes_hold_and_do_not_modify_legacy_report() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        tracks = session.execute(playlist_tracks_table.select()).mappings().all()
        store.tamper(tracks[0]["audio_uri"], b"forged bytes")
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(
                session, store, pkg.media_package_id, apply=True, now=NOW,
            )
        assert exc.value.code == "source_checksum_mismatch"
        stored = get_media_package_by_id(session, pkg.media_package_id)
        assert stored is not None and "evidence" not in stored.render_report["album_texture"]


def test_missing_link_to_original_source_is_not_guessed() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store, corrupt_track=True)
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(
                session, store, pkg.media_package_id, apply=True, now=NOW,
            )
        assert exc.value.code == "album_catalog_source_mismatch"
        stored = get_media_package_by_id(session, pkg.media_package_id)
        assert stored is not None and "evidence" not in stored.render_report["album_texture"]


@pytest.mark.parametrize(("field", "changed"), [
    ("experiment_id", "unrelated_experiment"),
    ("content_arm_id", "unrelated_arm"),
])
def test_backfill_refuses_mismatched_playlist_lineage(field: str, changed: str) -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        session.execute(
            playlist_sessions_table.update()
            .where(playlist_sessions_table.c.id == pkg.playlist_session_id)
            .values(**{field: changed})
        )
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(session, store, pkg.media_package_id, apply=True, now=NOW)
        assert exc.value.code == "album_catalog_source_mismatch"
        stored = get_media_package_by_id(session, pkg.media_package_id)
        assert stored is not None
        assert "evidence" not in stored.render_report["album_texture"]


def test_backfill_refuses_noncontiguous_original_playlist_positions() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        session.execute(
            playlist_tracks_table.update()
            .where(playlist_tracks_table.c.song_id == "song_2")
            .values(position=7)
        )
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(session, store, pkg.media_package_id, apply=True, now=NOW)
        assert exc.value.code == "album_catalog_source_mismatch"


def test_existing_evidence_swapped_track_order_is_not_idempotent() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        assert backfill_package_texture(
            session, store, pkg.media_package_id, apply=True, now=NOW,
        ).applied
        stored = get_media_package_by_id(session, pkg.media_package_id)
        assert stored is not None
        altered_report = json.loads(json.dumps(stored.render_report))
        altered_report["album_texture"]["evidence"]["track_signatures"].reverse()
        session.execute(
            media_packages_table.update()
            .where(media_packages_table.c.media_package_id == pkg.media_package_id)
            .values(render_report_json=json.dumps(altered_report))
        )
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(session, store, pkg.media_package_id, apply=True, now=NOW)
        assert exc.value.code == "album_catalog_backfill_conflict"
        current = get_media_package_by_id(session, pkg.media_package_id)
        assert current is not None and current.render_report == altered_report


def test_unknown_package_refused() -> None:
    with media_db_session() as session:
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(
                session, InMemoryArtifactStore(), "unrecognized", apply=True, now=NOW,
            )
        assert exc.value.code == "album_catalog_package_missing"


def test_explicit_status_and_timezone_required() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        with pytest.raises(ValueError, match="timezone"):
            backfill_package_texture(
                session, store, pkg.media_package_id,
                apply=True, now=datetime(2026, 10, 23),
            )
        session.execute(
            media_packages_table.update()
            .where(media_packages_table.c.media_package_id == pkg.media_package_id)
            .values(status=PackageStatus.FAILED.value)
        )
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(
                session, store, pkg.media_package_id, apply=True, now=NOW,
            )
        assert exc.value.code == "album_catalog_package_not_eligible"


def test_report_modified_while_sources_are_verified_is_not_overwritten() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        before_fetch = store.fetch_verified
        modified = False

        def concurrent_change(uri: str, checksum: str) -> bytes:
            nonlocal modified
            if not modified:
                session.execute(
                    media_packages_table.update()
                    .where(media_packages_table.c.media_package_id == pkg.media_package_id)
                    .values(render_report_json=json.dumps({"operator_revision": "changed"}))
                )
                modified = True
            return before_fetch(uri, checksum)

        with (
            patch.object(store, "fetch_verified", side_effect=concurrent_change),
            pytest.raises(MediaAssemblyError) as exc,
        ):
            backfill_package_texture(
                session, store, pkg.media_package_id, apply=True, now=NOW
            )
        assert exc.value.code == "album_catalog_backfill_conflict"
        current = get_media_package_by_id(session, pkg.media_package_id)
        assert current is not None and current.render_report == {"operator_revision": "changed"}


@pytest.mark.parametrize(("field", "replacement"), [
    ("audio_uri", "media://swapped"),
    ("audio_checksum", "f" * 64),
    ("source_artifact_id", "different_source"),
    ("position", 8),
])
def test_source_track_mutation_during_measurement_refuses_commit(
    field: str, replacement: str | int,
) -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        original_fetch = store.fetch_verified
        changed = False

        def mutate_after_snapshot(uri: str, checksum: str) -> bytes:
            nonlocal changed
            if not changed:
                session.execute(
                    playlist_tracks_table.update()
                    .where(playlist_tracks_table.c.song_id == "song_0")
                    .values(**{field: replacement})
                )
                changed = True
            return original_fetch(uri, checksum)

        with (
            patch.object(store, "fetch_verified", side_effect=mutate_after_snapshot),
            pytest.raises(MediaAssemblyError) as exc,
        ):
            backfill_package_texture(session, store, pkg.media_package_id, apply=True, now=NOW)
        assert changed and exc.value.code == "album_catalog_backfill_conflict"
        saved = get_media_package_by_id(session, pkg.media_package_id)
        assert saved is not None and "evidence" not in saved.render_report["album_texture"]


@pytest.mark.parametrize("source_change", [
    "insert_track", "delete_track", "playlist_identity", "package_tracklist",
])
def test_source_catalog_changes_during_measurement_refuse_commit(
    source_change: str,
) -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        original_fetch = store.fetch_verified
        changed = False

        def mutate_after_snapshot(uri: str, checksum: str) -> bytes:
            nonlocal changed
            if not changed:
                if source_change == "insert_track":
                    extra = PlaylistTrack.create(
                        pkg.playlist_session_id, "unexpected_song", "unexpected_artifact",
                        9, 180.0, 240.0, audio_uri="media://unexpected",
                        audio_checksum="e" * 64, now=NOW,
                    )
                    insert_playlist_track(session, extra)
                elif source_change == "delete_track":
                    session.execute(
                        playlist_tracks_table.delete().where(
                            playlist_tracks_table.c.song_id == "song_2",
                        )
                    )
                elif source_change == "playlist_identity":
                    session.execute(
                        playlist_sessions_table.update()
                        .where(playlist_sessions_table.c.id == pkg.playlist_session_id)
                        .values(experiment_id="changed_during_measurement")
                    )
                else:
                    session.execute(
                        media_packages_table.update()
                        .where(media_packages_table.c.media_package_id == pkg.media_package_id)
                        .values(tracklist_json=json.dumps([{"position": 1}]))
                    )
                changed = True
            return original_fetch(uri, checksum)

        with (
            patch.object(store, "fetch_verified", side_effect=mutate_after_snapshot),
            pytest.raises(MediaAssemblyError) as exc,
        ):
            backfill_package_texture(session, store, pkg.media_package_id, apply=True, now=NOW)
        assert changed and exc.value.code == "album_catalog_backfill_conflict"
        saved = get_media_package_by_id(session, pkg.media_package_id)
        assert saved is not None and "evidence" not in saved.render_report["album_texture"]


def test_existing_incompatible_evidence_is_not_silently_accepted_or_overwritten() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        session.execute(
            media_packages_table.update()
            .where(media_packages_table.c.media_package_id == pkg.media_package_id)
            .values(render_report_json=json.dumps({
                "album_texture": {
                    "evidence": {
                        "policy_version": "album-texture-v0",
                        "extractor_version": DEFAULT_FEATURE_POLICY.version,
                        "track_signatures": [{"candidate_id": "old"}],
                    },
                },
            }))
        )
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(
                session, store, pkg.media_package_id, apply=True, now=NOW,
            )
        assert exc.value.code == "album_catalog_version_incompatible"
        current = get_media_package_by_id(session, pkg.media_package_id)
        assert current is not None
        assert current.render_report["album_texture"]["evidence"]["policy_version"] == (
            "album-texture-v0"
        )


def test_existing_zero_norm_signature_cannot_be_reused_as_valid_evidence() -> None:
    with media_db_session() as session:
        store = InMemoryArtifactStore()
        pkg = _legacy(session, store)
        assert backfill_package_texture(
            session, store, pkg.media_package_id, apply=True, now=NOW,
        ).applied
        saved = get_media_package_by_id(session, pkg.media_package_id)
        assert saved is not None
        altered = json.loads(json.dumps(saved.render_report))
        altered["album_texture"]["evidence"]["track_signatures"][0]["signature"] = (
            [0.0] * DEFAULT_ALBUM_TEXTURE_POLICY.expected_signature_bins
        )
        session.execute(
            media_packages_table.update()
            .where(media_packages_table.c.media_package_id == pkg.media_package_id)
            .values(render_report_json=json.dumps(altered))
        )
        with pytest.raises(MediaAssemblyError) as exc:
            backfill_package_texture(
                session, store, pkg.media_package_id, apply=True, now=NOW,
            )
        assert exc.value.code == "album_catalog_unmeasurable"
        current = get_media_package_by_id(session, pkg.media_package_id)
        assert current is not None and current.render_report == altered


def test_batch_backfill_isolates_success_refusal_and_dry_run() -> None:
    engine = create_media_engine(static_pool=True)
    store = InMemoryArtifactStore()
    try:
        with media_session(engine) as seed:
            first = _legacy(seed, store, suffix="_first")
            second = _legacy(seed, store, suffix="_second")
            seed.commit()
        factory = lambda: media_session(engine)

        inspected = backfill_packages_texture(
            factory, store, [first.media_package_id, "missing", second.media_package_id],
            apply=False, now=NOW,
        )
        assert len(inspected.succeeded) == 2
        assert all(not item.applied for item in inspected.succeeded)
        assert inspected.refused == (("missing", "album_catalog_package_missing"),)
        with factory() as session:
            for package_id in (first.media_package_id, second.media_package_id):
                pkg = get_media_package_by_id(session, package_id)
                assert pkg is not None and "evidence" not in pkg.render_report["album_texture"]

        written = backfill_packages_texture(
            factory, store, [first.media_package_id, "missing", second.media_package_id],
            apply=True, now=NOW,
        )
        assert [item.media_package_id for item in written.succeeded] == [
            first.media_package_id, second.media_package_id,
        ]
        assert all(item.applied for item in written.succeeded)
        assert written.refused == (("missing", "album_catalog_package_missing"),)
        with factory() as session:
            for package_id in (first.media_package_id, second.media_package_id):
                pkg = get_media_package_by_id(session, package_id)
                assert pkg is not None and len(
                    pkg.render_report["album_texture"]["evidence"]["track_signatures"]
                ) == 3
        repeated = backfill_packages_texture(
            factory, store, [first.media_package_id, second.media_package_id],
            apply=True, now=NOW,
        )
        assert len(repeated.succeeded) == 2
        assert all(item.status == "already_present" for item in repeated.succeeded)
    finally:
        engine.dispose()


def test_batch_rejects_duplicates_and_unbounded_selection_before_writes() -> None:
    engine = create_media_engine(static_pool=True)
    store = InMemoryArtifactStore()
    try:
        factory = lambda: media_session(engine)
        with pytest.raises(ValueError, match="duplicate"):
            backfill_packages_texture(
                factory, store, ["same", "same"], apply=True, now=NOW,
            )
        with pytest.raises(ValueError, match="1..20"):
            backfill_packages_texture(
                factory, store, [f"package_{i}" for i in range(21)],
                apply=True, now=NOW,
            )
        with pytest.raises(ValueError, match="nonempty"):
            backfill_packages_texture(factory, store, [" "], apply=True, now=NOW)
    finally:
        engine.dispose()


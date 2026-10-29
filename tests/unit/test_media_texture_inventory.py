"""Read-only bounded P6 catalog inventory: metadata only, never MinIO."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from media_factory.audio.features import (
    DEFAULT_FEATURE_POLICY,
    measure_features,
)
from media_factory.domain.enums import PackageStatus, PlaylistStatus
from media_factory.domain.models import MediaPackage
from media_factory.playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY
from media_factory.storage import models as media_tables
from media_factory.storage.models import media_packages_table
from media_factory.storage.repositories import insert_media_package
from media_factory.texture_inventory import inspect_brand_album_textures
from plm_platform.cli import app
from typer.testing import CliRunner

from tests.support.media_audio import synth_track
from tests.support.media_db import create_media_engine, media_db_session, media_session
from tests.support.seams import sample_lineage

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def _package(suffix: str, brand: str, *, report: dict, status=PackageStatus.READY_FOR_PUBLISH):
    return MediaPackage.create(
        release_manifest_id="rel_" + suffix,
        experiment_id="exp_" + suffix,
        content_arm_id="arm_" + suffix,
        brand_id=brand,
        playlist_session_id="session_" + suffix,
        visual_session_id="visual_" + suffix,
        video_master_uri="media://video", video_checksum="v",
        video_duration_seconds=180.0,
        render_profile="preview_360p_v1",
        audio_master_uri="media://audio", audio_checksum="a",
        thumbnails=[],
        tracklist=[{"position": 1, "song_id": "song_" + suffix,
                    "source_artifact_id": "source_" + suffix}],
        chapters=[], metadata_candidates=[], short_source_assets=[],
        render_report=report, status=status, lineage=sample_lineage(), now=NOW,
    )


def _evidence(suffix: str) -> dict:
    signature = measure_features("song_" + suffix, synth_track(seconds=4)).texture_signature
    assert signature is not None
    return {
        "policy_version": DEFAULT_ALBUM_TEXTURE_POLICY.version,
        "extractor_version": DEFAULT_FEATURE_POLICY.version,
        "track_signatures": [{
            "candidate_id": "song_" + suffix, "artifact_sha256": "a" * 64,
            "signature": list(signature),
        }],
    }


def _seed_original_metadata(
    session, package: MediaPackage, *, checksum: str = "a" * 64,
) -> None:
    """Populate P6 recorded sources without storing or reading audio bytes."""
    media_tracks = package.tracklist
    assert len(media_tracks) == 1
    media_record = media_tracks[0]
    session.execute(media_tables.playlist_sessions_table.insert().values(
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
    session.execute(media_tables.playlist_tracks_table.insert().values(
        id="original_" + package.playlist_session_id,
        playlist_session_id=package.playlist_session_id,
        song_id=media_record["song_id"],
        source_artifact_id=media_record["source_artifact_id"],
        position=0,
        start_offset_seconds=0.0,
        end_offset_seconds=4.0,
        transition_type="short_crossfade",
        transition_duration_seconds=2.0,
        sequence_reason="test",
        title="Original",
        audio_uri="memory://original-source",
        audio_checksum=checksum,
        created_at=NOW,
    ))


def test_inventory_pagination_is_stable_bounded_and_read_only() -> None:
    with media_db_session() as session:
        packages = [
            _package("01", "brand_a", report={"album_texture": {"scope": "legacy"}}),
            _package("02", "brand_a", report={"album_texture": {"evidence": _evidence("02")}}),
            _package("03", "brand_a", report={"album_texture": {
                "evidence": {**_evidence("03"), "extractor_version": "old"}
            }}),
            _package("04", "brand_a", report={"album_texture": {"evidence": {
                **_evidence("04"),
                "track_signatures": [{**_evidence("04")["track_signatures"][0],
                                     "signature": [0.0] * 192}],
            }}}),
            _package("05", "brand_b", report={"album_texture": {"scope": "legacy"}}),
            _package("06", "brand_a", report={}, status=PackageStatus.FAILED),
        ]
        for p in packages:
            insert_media_package(session, p)
        _seed_original_metadata(session, packages[1])
        before = session.execute(
            media_packages_table.select().with_only_columns(
                media_packages_table.c.media_package_id,
                media_packages_table.c.render_report_json,
                media_packages_table.c.updated_at,
            )
        ).all()
        first = inspect_brand_album_textures(session, "brand_a", page_size=2)
        assert first.has_more and first.next_after_id is not None
        second = inspect_brand_album_textures(
            session, "brand_a", page_size=2, after_package_id=first.next_after_id,
        )
        assert not second.has_more and second.next_after_id is None
        all_entries = (*first.entries, *second.entries)
        assert [item.package_id for item in all_entries] == sorted(
            item.package_id for item in all_entries
        )
        by_id = {entry.package_id: entry for entry in all_entries}
        assert by_id[packages[0].media_package_id].classification == "missing_evidence"
        assert by_id[packages[1].media_package_id].classification == "evidence_structure_valid"
        assert by_id[packages[2].media_package_id].classification == "incompatible_version"
        assert by_id[packages[3].media_package_id].classification == "invalid_evidence"
        assert by_id[packages[2].media_package_id].reason_code == (
            "album_catalog_version_incompatible"
        )
        assert by_id[packages[3].media_package_id].reason_code == (
            "album_catalog_unmeasurable"
        )
        after = session.execute(
            media_packages_table.select().with_only_columns(
                media_packages_table.c.media_package_id,
                media_packages_table.c.render_report_json,
                media_packages_table.c.updated_at,
            )
        ).all()
        assert before == after


def test_inventory_does_not_trust_evidence_without_matching_original_sources() -> None:
    with media_db_session() as session:
        packages = [
            _package(suffix, "brand_provenance", report={
                "album_texture": {"evidence": _evidence(suffix)}
            })
            for suffix in ("missing", "tampered", "identity")
        ]
        for item in packages:
            insert_media_package(session, item)
        _seed_original_metadata(session, packages[1], checksum="b" * 64)
        _seed_original_metadata(session, packages[2])
        session.execute(
            media_tables.playlist_sessions_table.update()
            .where(media_tables.playlist_sessions_table.c.id == packages[2].playlist_session_id)
            .values(content_arm_id="changed_after_render")
        )

        entries = inspect_brand_album_textures(session, "brand_provenance").entries
        assert len(entries) == 3
        assert all(item.classification == "source_metadata_unverifiable" for item in entries)
        assert all(item.reason_code == "album_catalog_source_mismatch" for item in entries)


def test_inventory_rejects_unbounded_or_unselected_queries() -> None:
    with media_db_session() as session:
        for kwargs in (
            {"brand_id": ""}, {"brand_id": " "}, {"brand_id": "x", "page_size": 0},
            {"brand_id": "x", "page_size": 101},
            {"brand_id": "x", "after_package_id": ""},
        ):
            with pytest.raises(ValueError):
                inspect_brand_album_textures(session, **kwargs)


def test_inventory_reports_malformed_json_without_guessing() -> None:
    with media_db_session() as session:
        package = _package("bad", "brand_a", report={})
        insert_media_package(session, package)
        session.execute(
            media_packages_table.update()
            .where(media_packages_table.c.media_package_id == package.media_package_id)
            .values(render_report_json="{invalid-json", tracklist_json=json.dumps([]))
        )
        result = inspect_brand_album_textures(session, "brand_a")
        assert len(result.entries) == 1
        assert result.entries[0].classification == "unreadable_record"


def test_inventory_cli_reads_only_selected_brand_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from media_factory import cli as media_cli

    engine = create_media_engine(static_pool=True)
    try:
        with media_session(engine) as session:
            legacy = _package("legacy", "brand_a", report={"album_texture": {"scope": "legacy"}})
            other = _package("other", "brand_b", report={"album_texture": {"scope": "legacy"}})
            insert_media_package(session, legacy)
            insert_media_package(session, other)
            session.commit()

        monkeypatch.setattr(media_cli, "platform_session_factory", lambda: lambda: media_session(engine))
        result = CliRunner().invoke(
            app, ["media", "inspect-album-textures", "--brand-id", "brand_a"],
        )
        assert result.exit_code == 0, result.output
        assert legacy.media_package_id in result.output
        assert other.media_package_id not in result.output
        assert "missing_evidence" in result.output
        assert "NOT verified" in result.output
        with media_session(engine) as session:
            stored = session.execute(
                media_packages_table.select().with_only_columns(
                    media_packages_table.c.render_report_json
                ).where(
                    media_packages_table.c.media_package_id == legacy.media_package_id
                )
            ).scalar_one()
            assert json.loads(stored) == legacy.render_report
    finally:
        engine.dispose()


def test_inventory_cli_help_and_required_brand() -> None:
    runner = CliRunner()
    help_result = runner.invoke(app, ["media", "inspect-album-textures", "--help"])
    assert help_result.exit_code == 0
    assert "--brand-id" in help_result.output
    assert "--after-id" in help_result.output
    assert "--limit" in help_result.output
    missing_brand = runner.invoke(app, ["media", "inspect-album-textures"])
    assert missing_brand.exit_code != 0

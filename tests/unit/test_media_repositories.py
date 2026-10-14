"""Unit tests for media storage repositories with in-memory SQLite harness."""

from __future__ import annotations

from datetime import UTC, datetime

from media_factory.domain.enums import (
    PackageStatus,
    PlaylistStatus,
    VisualAssetType,
)
from media_factory.domain.models import (
    MediaPackage,
    PlaylistSession,
    PlaylistTrack,
    RenderJob,
    VisualAsset,
    VisualSession,
)
from media_factory.storage.repositories import (
    get_media_package_by_id,
    get_media_package_by_release_manifest,
    get_playlist_session,
    get_render_job,
    get_visual_session,
    insert_media_package,
    insert_playlist_session,
    insert_playlist_track,
    insert_render_job,
    insert_visual_asset,
    insert_visual_session,
    update_media_package_status,
    update_playlist_session_status,
)

from tests.support.media_db import media_db_session
from tests.support.seams import sample_lineage

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

def test_playlist_session_and_tracks_repository() -> None:
    with media_db_session() as session:
        pl = PlaylistSession.create("rm_01", "exp_01", "arm_01", "brand_01", 300.0, now=NOW)
        insert_playlist_session(session, pl)

        t1 = PlaylistTrack.create(pl.id, "song_01", "art_01", 0, 0.0, 150.0, title="T1", now=NOW)
        t2 = PlaylistTrack.create(pl.id, "song_02", "art_02", 1, 148.0, 300.0, title="T2", now=NOW)
        insert_playlist_track(session, t1)
        insert_playlist_track(session, t2)

        fetched = get_playlist_session(session, pl.id)
        assert fetched is not None
        assert fetched.id == pl.id
        assert len(fetched.tracks) == 2
        assert fetched.tracks[0].title == "T1"

        update_playlist_session_status(session, pl.id, PlaylistStatus.ASSEMBLED, now=NOW)
        refetched = get_playlist_session(session, pl.id)
        assert refetched is not None
        assert refetched.status == PlaylistStatus.ASSEMBLED


def test_visual_session_and_assets_repository() -> None:
    with media_db_session() as session:
        vs = VisualSession.create(
            "ps_01", "brand_01", "v1", "cyber_v1", visual_plan_json={"scenes": []}, now=NOW
        )
        insert_visual_session(session, vs)

        a1 = VisualAsset.create(vs.id, VisualAssetType.BACKGROUND, "media/bg1.svg", "chk_1", now=NOW)
        insert_visual_asset(session, a1)

        fetched = get_visual_session(session, vs.id)
        assert fetched is not None
        assert fetched.id == vs.id
        assert len(fetched.assets) == 1
        assert fetched.assets[0].uri == "media/bg1.svg"


def test_media_package_and_render_job_repository() -> None:
    with media_db_session() as session:
        pkg = MediaPackage.create(
            release_manifest_id="rm_repo_01",
            experiment_id="exp_repo_01",
            content_arm_id="arm_repo_01",
            brand_id="brand_neon",
            playlist_session_id="ps_repo_01",
            visual_session_id="vs_repo_01",
            video_master_uri="s3://v.mp4",
            video_checksum="v_chk",
            video_duration_seconds=300.0,
            render_profile="yt_longform_1080p_v1",
            audio_master_uri="s3://a.wav",
            audio_checksum="a_chk",
            thumbnails=[{"id": "t1", "uri": "s3://t1.webp"}],
            tracklist=[{"song_id": "s1"}],
            chapters=[{"title": "C1"}],
            metadata_candidates=[{"variant_id": "m1"}],
            short_source_assets=[],
            render_report={"status": "passed"},
            lineage=sample_lineage(),
            status=PackageStatus.READY_FOR_PUBLISH,
            now=NOW,
        )
        insert_media_package(session, pkg)

        rjob = RenderJob.create(pkg.media_package_id, "yt_longform_1080p_v1", now=NOW)
        insert_render_job(session, rjob)

        fetched_pkg = get_media_package_by_id(session, pkg.media_package_id)
        assert fetched_pkg is not None
        assert fetched_pkg.media_package_id == pkg.media_package_id
        assert fetched_pkg.release_manifest_id == "rm_repo_01"

        fetched_by_rm = get_media_package_by_release_manifest(session, "rm_repo_01")
        assert fetched_by_rm is not None
        assert fetched_by_rm.media_package_id == pkg.media_package_id

        fetched_rj = get_render_job(session, rjob.id)
        assert fetched_rj is not None
        assert fetched_rj.id == rjob.id

        update_media_package_status(
            session, pkg.media_package_id, PackageStatus.PUBLISHED, now=NOW
        )
        refetched_pkg = get_media_package_by_id(session, pkg.media_package_id)
        assert refetched_pkg is not None
        assert refetched_pkg.status == PackageStatus.PUBLISHED

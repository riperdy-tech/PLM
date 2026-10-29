"""PostgreSQL-only P6 backfill races on an isolated disposable test database.

Requires PLM_BACKFILL_PG_ISOLATED=1 and a configured local test-capable
PostgreSQL role with CREATEDB. Does not write to the configured PLM database's
tables: fixtures, DML and cleanup use a newly created random test database.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from media_factory.domain.models import MediaPackage, PlaylistSession, PlaylistTrack
from media_factory.errors import MediaAssemblyError
from media_factory.storage.models import (
    MEDIA_METADATA,
    playlist_sessions_table,
    playlist_tracks_table,
)
from media_factory.storage.repositories import (
    get_media_package_by_id,
    insert_media_package,
    insert_playlist_session,
    insert_playlist_track,
)
from media_factory.texture_backfill import backfill_package_texture
from plm_platform.config import load_settings
from plm_platform.db import database_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.support.media_audio import synth_track
from tests.support.media_backend import InMemoryArtifactStore, synth_specs
from tests.support.seams import sample_lineage

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 24, tzinfo=UTC)


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[sa.Engine]:
    if os.getenv("PLM_BACKFILL_PG_ISOLATED") != "1":
        pytest.skip("explicit PLM_BACKFILL_PG_ISOLATED=1 required for disposable PostgreSQL DB")
    url = database_url(load_settings(dotenv_path=Path(".env")))
    if url.get_backend_name() != "postgresql":
        pytest.skip("requires PostgreSQL")
    root = sa.create_engine(
        url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5},
    )
    name = f"plm_backfill_probe_{uuid4().hex[:16]}"
    quoted_name = f'"{name}"'  # generated solely from [a-z0-9_]
    created = False
    probe: sa.Engine | None = None
    try:
        with root.connect() as conn:
            conn.exec_driver_sql(f"CREATE DATABASE {quoted_name}")
        created = True
        probe = sa.create_engine(
            url.set(database=name), connect_args={"connect_timeout": 5},
        )
        with probe.begin() as conn:
            conn.exec_driver_sql("CREATE SCHEMA media")
        MEDIA_METADATA.create_all(probe)
        yield probe
    finally:
        if probe is not None:
            probe.dispose()
        if created:
            with root.connect() as conn:
                conn.exec_driver_sql(f"DROP DATABASE {quoted_name} WITH (FORCE)")
        root.dispose()


def _seed(engine: sa.Engine, suffix: str) -> tuple[str, InMemoryArtifactStore]:
    store = InMemoryArtifactStore()
    with Session(engine) as session:
        playlist = PlaylistSession.create(
            "release_" + suffix, "exp_" + suffix, "arm_" + suffix,
            "brand_test", 180, now=NOW,
        )
        insert_playlist_session(session, playlist)
        tracklist = []
        for i, spec in enumerate(synth_specs()):
            song = f"song_{i}_{suffix}"
            artifact = store.put_source(song, synth_track(seconds=4, **spec))
            track = PlaylistTrack.create(
                playlist.id, song, f"artifact_{i}_{suffix}", i,
                i * 60.0, (i + 1) * 60.0,
                title=f"Track {i + 1}", audio_uri=artifact.uri,
                audio_checksum=artifact.sha256, now=NOW,
            )
            insert_playlist_track(session, track)
            tracklist.append({
                "position": i + 1, "song_id": song, "title": track.title,
                "source_artifact_id": track.source_artifact_id,
            })
        package = MediaPackage.create(
            release_manifest_id="release_" + suffix,
            experiment_id="exp_" + suffix,
            content_arm_id="arm_" + suffix,
            brand_id="brand_test",
            playlist_session_id=playlist.id,
            visual_session_id="visual_" + suffix,
            video_master_uri="media://video", video_checksum="video",
            video_duration_seconds=180.0, render_profile="preview_360p_v1",
            audio_master_uri="media://audio", audio_checksum="audio",
            thumbnails=[], tracklist=tracklist, chapters=[],
            metadata_candidates=[], short_source_assets=[],
            render_report={"qa": {"status": "passed"}, "album_texture": {"scope": "legacy"}},
            lineage=sample_lineage(), now=NOW,
        )
        insert_media_package(session, package)
        session.commit()
        return package.media_package_id, store


def test_another_transaction_changes_source_after_snapshot_before_lock(
    pg_engine: sa.Engine,
) -> None:
    package_id, store = _seed(pg_engine, "change")
    original_fetch = store.fetch_verified
    changed = False

    def concurrent_commit(uri: str, checksum: str) -> bytes:
        nonlocal changed
        if not changed:
            with pg_engine.begin() as other:
                other.execute(
                    playlist_tracks_table.update()
                    .where(playlist_tracks_table.c.song_id == "song_0_change")
                    .values(audio_uri="media://replaced")
                )
            changed = True
        return original_fetch(uri, checksum)

    from unittest.mock import patch

    with Session(pg_engine) as session:
        with (
            patch.object(store, "fetch_verified", side_effect=concurrent_commit),
            pytest.raises(MediaAssemblyError) as exc,
        ):
            backfill_package_texture(session, store, package_id, apply=True, now=NOW)
        assert changed and exc.value.code == "album_catalog_backfill_conflict"
        session.rollback()
        saved = get_media_package_by_id(session, package_id)
        assert saved is not None and "evidence" not in saved.render_report["album_texture"]


@pytest.mark.parametrize("operation", [
    "track_update", "track_insert", "track_delete", "playlist_update",
])
def test_source_writes_blocked_until_backfill_transaction_commits(
    pg_engine: sa.Engine, operation: str,
) -> None:
    package_id, store = _seed(pg_engine, operation)
    song = f"song_0_{operation}"
    with pg_engine.connect() as conn:
        first = conn.execute(
            sa.select(playlist_tracks_table)
            .where(playlist_tracks_table.c.song_id == song)
        ).mappings().one()
        extra = dict(first)
        extra.update(id=f"extra_{operation}", song_id=f"new_{operation}", position=8)

    with Session(pg_engine) as session:
        result = backfill_package_texture(session, store, package_id, apply=True, now=NOW)
        assert result.applied

        def competing_writer() -> str | None:
            with pg_engine.begin() as conn:
                conn.exec_driver_sql("SET LOCAL lock_timeout = '300ms'")
                try:
                    if operation == "track_update":
                        stmt = (
                            playlist_tracks_table.update()
                            .where(playlist_tracks_table.c.song_id == song)
                            .values(audio_uri="media://late-update")
                        )
                    elif operation == "track_insert":
                        stmt = playlist_tracks_table.insert().values(**extra)
                    elif operation == "track_delete":
                        stmt = (
                            playlist_tracks_table.delete()
                            .where(playlist_tracks_table.c.song_id == song)
                        )
                    else:
                        stmt = (
                            playlist_sessions_table.update()
                            .where(playlist_sessions_table.c.experiment_id == f"exp_{operation}")
                            .values(content_arm_id="modified")
                        )
                    conn.execute(stmt)
                except DBAPIError as exc:
                    return getattr(exc.orig, "sqlstate", None)
                return None

        with ThreadPoolExecutor(max_workers=1) as pool:
            state = pool.submit(competing_writer).result(timeout=10)
        assert state == "55P03", "source write must wait while PostgreSQL SHARE lock is held"
        session.commit()

    with pg_engine.begin() as conn:
        result = conn.execute(
            playlist_tracks_table.update()
            .where(playlist_tracks_table.c.song_id == song)
            .values(audio_uri="media://after-commit")
        )
        assert result.rowcount == 1
    with Session(pg_engine) as session:
        saved = get_media_package_by_id(session, package_id)
        assert saved is not None and "evidence" in saved.render_report["album_texture"]

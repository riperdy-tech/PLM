"""Operator-controlled P6 legacy audio texture backfill.

Uses immutable P6 playlist-track URI/SHA256 recorded when the album was
assembled. Never guesses a source from a title, searches by name, or calls an
external generation/publishing provider. Dry-run is the default at the CLI.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .artifacts import ArtifactStore
from .audio.features import measure_features
from .domain.enums import PackageStatus
from .errors import MediaAssemblyError, MediaInfrastructureError
from .playlist.catalog import (
    CatalogAlbum,
    serialize_album_signatures,
    validate_catalog_album,
)
from .playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY, check_album_texture
from .storage.models import media_packages_table, playlist_sessions_table, playlist_tracks_table
from .storage.repositories import get_media_package_by_id, get_playlist_session


@dataclass(frozen=True, slots=True)
class TextureBackfillResult:
    media_package_id: str
    track_count: int
    status: str
    applied: bool


def backfill_package_texture(
    session: Session,
    store: ArtifactStore,
    package_id: str,
    *,
    apply: bool,
    now: datetime,
) -> TextureBackfillResult:
    """Check archived source facts, remeasure audio, optionally update one P6 row.

    Requires an explicit caller-selected package. The update is conditional on
    the original render_report bytes and READY/PUBLISHED status: concurrent
    changes cannot be overwritten. Caller controls commit/rollback.
    """
    if not package_id:
        raise ValueError("package_id required")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    pkg = get_media_package_by_id(session, package_id)
    if pkg is None:
        raise MediaAssemblyError(
            "album_catalog_package_missing", f"package {package_id!r} not found", ["package_id"]
        )
    if pkg.status not in (PackageStatus.READY_FOR_PUBLISH, PackageStatus.PUBLISHED):
        raise MediaAssemblyError(
            "album_catalog_package_not_eligible",
            f"package {package_id!r} is not ready or published", ["package_id"]
        )
    # Capture the persisted package tracklist before any expensive source reads.
    # It is a separate legacy text column and must not change under the backfill.
    original_tracklist = session.execute(
        sa.select(media_packages_table.c.tracklist_json)
        .where(media_packages_table.c.media_package_id == package_id)
    ).scalar_one_or_none()
    if original_tracklist is None or json.loads(original_tracklist) != pkg.tracklist:
        raise MediaAssemblyError(
            "album_catalog_backfill_conflict",
            "package tracklist changed during verification", ["catalog"],
        )
    playlist = get_playlist_session(session, pkg.playlist_session_id)
    if (
        playlist is None or playlist.release_manifest_id != pkg.release_manifest_id
        or playlist.brand_id != pkg.brand_id
        or playlist.experiment_id != pkg.experiment_id
        or playlist.content_arm_id != pkg.content_arm_id
    ):
        raise MediaAssemblyError(
            "album_catalog_source_mismatch",
            "stored playlist identity does not match the archived package", ["catalog"]
        )
    tracks = sorted(playlist.tracks, key=lambda track: track.position)
    if (
        not tracks or len(tracks) != len(pkg.tracklist)
        or len({track.song_id for track in tracks}) != len(tracks)
    ):
        raise MediaAssemblyError(
            "album_catalog_source_mismatch",
            "archived tracklist is missing or contains duplicate source records", ["catalog"]
        )
    for i, (track, row) in enumerate(zip(tracks, pkg.tracklist, strict=True), 1):
        if (
            not isinstance(row, dict)
            or row.get("song_id") != track.song_id
            or row.get("source_artifact_id") != track.source_artifact_id
            or row.get("position") != i or track.position != i - 1
            or not track.audio_uri or len(track.audio_checksum) != 64
        ):
            raise MediaAssemblyError(
                "album_catalog_source_mismatch",
                f"archived source for track position {i} is not verifiable", ["catalog"]
            )

    texture = pkg.render_report.get("album_texture")
    if texture is not None and not isinstance(texture, dict):
        raise MediaAssemblyError(
            "album_catalog_backfill_conflict",
            "existing album texture report has unsupported structure", ["catalog"]
        )
    evidence = texture.get("evidence") if isinstance(texture, dict) else None
    if evidence is not None:
        if not isinstance(evidence, dict) or not isinstance(evidence.get("track_signatures"), list):
            raise MediaAssemblyError(
                "album_catalog_backfill_conflict",
                "existing album evidence is malformed; do not overwrite it", ["catalog"]
            )
        prior = validate_catalog_album(
            CatalogAlbum(
                pkg.media_package_id, pkg.release_manifest_id,
                evidence["track_signatures"],
                policy_version=evidence.get("policy_version"),
                extractor_version=evidence.get("extractor_version"),
            ),
            policy=DEFAULT_ALBUM_TEXTURE_POLICY,
        )
        # The evidence is ordered by the saved playlist. A set comparison
        # would wrongly accept swapped signatures as an idempotent backfill.
        if [(candidate, checksum) for candidate, checksum, _ in prior] != [
            (t.song_id, t.audio_checksum) for t in tracks
        ]:
            raise MediaAssemblyError(
                "album_catalog_backfill_conflict",
                "existing evidence contradicts stored original source records", ["catalog"]
            )
        return TextureBackfillResult(package_id, len(tracks), "already_present", False)

    features = {}
    for track in tracks:
        # Each read is SHA256-verified by ArtifactStore. Never retain the audio
        # after measurement, and never replace missing data with blank texture.
        audio = store.fetch_verified(track.audio_uri, track.audio_checksum)
        features[track.song_id] = measure_features(track.song_id, audio)

    selected_ids = [track.song_id for track in tracks]
    within = check_album_texture(selected_ids, features)
    evidence_new = serialize_album_signatures(
        selected_ids, features, {t.song_id: t.audio_checksum for t in tracks}
    )
    if not apply:
        return TextureBackfillResult(package_id, len(tracks), "validated_dry_run", False)

    report = dict(pkg.render_report)
    existing_texture = dict(texture) if isinstance(texture, dict) else {}
    existing_texture.update({
        **within,
        "evidence": evidence_new,
        "backfill": {
            "origin": "p6_persisted_playlist_audio",
            "measured_from_sha256_verified_sources": True,
            "at": now.isoformat(),
        },
    })
    report["album_texture"] = existing_texture

    # PostgreSQL READ COMMITTED does not protect source rows checked only by
    # subqueries in an UPDATE: another transaction could change them after the
    # UPDATE snapshot but before our commit. Acquire short-lived SHARE table
    # locks only after expensive audio analysis; PostgreSQL source INSERT,
    # UPDATE and DELETE then wait until this transaction commits/rolls back.
    # Keep both tables locked because playlist tracks have no FK to the header.
    # SQLite uses its existing single-writer transaction plus the CAS checks.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(sa.text(
            "LOCK TABLE media.playlist_sessions, media.playlist_tracks IN SHARE MODE"
        ))

    # Optimistic compare-and-set: a concurrent change cannot be silently lost.
    original_row = session.execute(
        media_packages_table.select()
        .with_only_columns(media_packages_table.c.render_report_json)
        .where(media_packages_table.c.media_package_id == package_id)
    ).scalar_one_or_none()
    if original_row is None:
        raise MediaAssemblyError("album_catalog_backfill_conflict", "package vanished", ["catalog"])
    if json.loads(original_row) != pkg.render_report:
        raise MediaAssemblyError(
            "album_catalog_backfill_conflict",
            "render report changed during verification", ["catalog"]
        )
    # Every source fact used for the measurement must still match when the
    # report is written. Check them IN the conditional UPDATE, not with a
    # separate preflight SELECT that could race another transaction.
    ps = playlist_sessions_table.c
    pt = playlist_tracks_table.c
    playlist_unchanged = sa.exists(
        sa.select(ps.id).where(
            ps.id == playlist.id,
            ps.release_manifest_id == playlist.release_manifest_id,
            ps.experiment_id == playlist.experiment_id,
            ps.content_arm_id == playlist.content_arm_id,
            ps.brand_id == playlist.brand_id,
            ps.status == playlist.status.value,
            ps.sequence_version == playlist.sequence_version,
            ps.sequence_checksum == playlist.sequence_checksum,
            ps.updated_at == playlist.updated_at,
        )
    )
    track_count_unchanged = (
        sa.select(sa.func.count())
        .select_from(playlist_tracks_table)
        .where(pt.playlist_session_id == playlist.id)
        .scalar_subquery() == len(tracks)
    )
    track_unchanged = [
        sa.exists(sa.select(pt.id).where(
            pt.id == track.id,
            pt.playlist_session_id == playlist.id,
            pt.song_id == track.song_id,
            pt.source_artifact_id == track.source_artifact_id,
            pt.position == track.position,
            pt.audio_uri == track.audio_uri,
            pt.audio_checksum == track.audio_checksum,
            pt.start_offset_seconds == track.start_offset_seconds,
            pt.end_offset_seconds == track.end_offset_seconds,
            pt.transition_type == track.transition_type.value,
            pt.transition_duration_seconds == track.transition_duration_seconds,
            pt.sequence_reason == track.sequence_reason,
            pt.title == track.title,
        ))
        for track in tracks
    ]
    updated = session.execute(
        media_packages_table.update()
        .where(media_packages_table.c.media_package_id == package_id)
        .where(media_packages_table.c.render_report_json == original_row)
        .where(media_packages_table.c.tracklist_json == original_tracklist)
        .where(media_packages_table.c.playlist_session_id == playlist.id)
        .where(media_packages_table.c.release_manifest_id == pkg.release_manifest_id)
        .where(media_packages_table.c.experiment_id == pkg.experiment_id)
        .where(media_packages_table.c.content_arm_id == pkg.content_arm_id)
        .where(media_packages_table.c.brand_id == pkg.brand_id)
        .where(media_packages_table.c.status == pkg.status.value)
        .where(playlist_unchanged, track_count_unchanged, *track_unchanged)
        .values(render_report_json=json.dumps(report, sort_keys=True), updated_at=now)
        .returning(media_packages_table.c.media_package_id)
    ).scalar_one_or_none()
    if updated != package_id:
        raise MediaAssemblyError(
            "album_catalog_backfill_conflict",
            "package changed during texture backfill; transaction must be rolled back", ["catalog"]
        )
    return TextureBackfillResult(package_id, len(tracks), "applied", True)


MAX_BACKFILL_BATCH_SIZE = 20


@dataclass(frozen=True, slots=True)
class TextureBackfillBatchResult:
    succeeded: tuple[TextureBackfillResult, ...]
    refused: tuple[tuple[str, str], ...]


def backfill_packages_texture(
    session_factory: Callable[[], Session],
    store: ArtifactStore,
    package_ids: Sequence[str],
    *,
    apply: bool,
    now: datetime,
) -> TextureBackfillBatchResult:
    """Process explicitly named albums, one isolated transaction per package.

    Each successful package is committed independently. A refused package is
    rolled back and reported, not silently skipped. Unexpected SQL failures
    abort the batch; dry runs never commit. No catalog-wide discovery is done.
    """
    ids = list(package_ids)
    if not 1 <= len(ids) <= MAX_BACKFILL_BATCH_SIZE:
        raise ValueError(f"specify 1..{MAX_BACKFILL_BATCH_SIZE} package IDs")
    if any(not isinstance(pkg_id, str) or not pkg_id.strip() for pkg_id in ids):
        raise ValueError("all package IDs must be nonempty")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate package IDs are not allowed")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    succeeded: list[TextureBackfillResult] = []
    refused: list[tuple[str, str]] = []
    for package_id in ids:
        with session_factory() as session:
            try:
                result = backfill_package_texture(
                    session, store, package_id, apply=apply, now=now,
                )
                if result.applied:
                    session.commit()
                else:
                    session.rollback()
            except (MediaAssemblyError, MediaInfrastructureError) as exc:
                session.rollback()
                refused.append((package_id, exc.code))
            except SQLAlchemyError:
                session.rollback()
                raise
            else:
                succeeded.append(result)
    return TextureBackfillBatchResult(tuple(succeeded), tuple(refused))

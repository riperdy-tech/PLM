"""Repository access functions for the `media` schema (PHASE-P6 §8, R7, R8, R9)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from plm_contracts.seams import ExperimentLineage
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import (
    PackageStatus,
    PlaylistStatus,
    RenderJobStatus,
    TransitionType,
    VisualAssetType,
)
from ..domain.models import (
    MediaPackage,
    PlaylistSession,
    PlaylistTrack,
    RenderJob,
    ThumbnailVariant,
    VisualAsset,
    VisualSession,
)
from ..errors import MediaAssemblyError
from ..playlist.catalog import (
    CatalogAlbum,
    ordered_track_evidence_matches,
    validate_catalog_album,
)
from ..playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY
from .models import (
    media_packages_table,
    playlist_sessions_table,
    playlist_tracks_table,
    render_jobs_table,
    thumbnail_variants_table,
    visual_assets_table,
    visual_sessions_table,
)
from .source_evidence import archived_sources_match

__all__ = [
    "get_media_package_by_id",
    "get_media_package_by_release_manifest",
    "get_playlist_session",
    "get_render_job",
    "get_visual_session",
    "insert_media_package",
    "insert_playlist_session",
    "insert_playlist_track",
    "insert_render_job",
    "insert_thumbnail_variant",
    "insert_visual_asset",
    "insert_visual_session",
    "list_catalog_albums",
    "list_playlist_tracks",
    "list_thumbnail_variants",
    "list_visual_assets",
    "update_media_package_status",
    "update_playlist_session_status",
    "update_render_job_status",
]


# 1. Playlist Sessions
def insert_playlist_session(session: Session, playlist: PlaylistSession) -> None:
    session.execute(
        playlist_sessions_table.insert().values(
            id=playlist.id,
            release_manifest_id=playlist.release_manifest_id,
            experiment_id=playlist.experiment_id,
            content_arm_id=playlist.content_arm_id,
            brand_id=playlist.brand_id,
            status=playlist.status.value,
            requested_duration_seconds=playlist.requested_duration_seconds,
            actual_duration_seconds=playlist.actual_duration_seconds,
            sequence_version=playlist.sequence_version,
            sequence_checksum=playlist.sequence_checksum,
            created_at=playlist.created_at,
            updated_at=playlist.updated_at,
        )
    )


def get_playlist_session(session: Session, session_id: str) -> PlaylistSession | None:
    row = (
        session.execute(
            playlist_sessions_table.select().where(playlist_sessions_table.c.id == session_id)
        )
        .mappings()
        .first()
    )
    if not row:
        return None
    tracks = list_playlist_tracks(session, session_id)
    return PlaylistSession(
        id=row["id"],
        release_manifest_id=row["release_manifest_id"],
        experiment_id=row["experiment_id"],
        content_arm_id=row["content_arm_id"],
        brand_id=row["brand_id"],
        status=PlaylistStatus(row["status"]),
        requested_duration_seconds=row["requested_duration_seconds"],
        actual_duration_seconds=row["actual_duration_seconds"],
        sequence_version=row["sequence_version"],
        sequence_checksum=row["sequence_checksum"],
        tracks=tracks,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def update_playlist_session_status(
    session: Session,
    session_id: str,
    status: PlaylistStatus,
    actual_duration: float | None = None,
    sequence_checksum: str | None = None,
    *,
    now: datetime,
) -> None:
    values: dict[str, Any] = {
        "status": status.value,
        "updated_at": now,
    }
    if actual_duration is not None:
        values["actual_duration_seconds"] = actual_duration
    if sequence_checksum is not None:
        values["sequence_checksum"] = sequence_checksum

    session.execute(
        playlist_sessions_table.update()
        .where(playlist_sessions_table.c.id == session_id)
        .values(**values)
    )


# 2. Playlist Tracks
def insert_playlist_track(session: Session, track: PlaylistTrack) -> None:
    session.execute(
        playlist_tracks_table.insert().values(
            id=track.id,
            playlist_session_id=track.playlist_session_id,
            song_id=track.song_id,
            source_artifact_id=track.source_artifact_id,
            position=track.position,
            start_offset_seconds=track.start_offset_seconds,
            end_offset_seconds=track.end_offset_seconds,
            transition_type=track.transition_type.value,
            transition_duration_seconds=track.transition_duration_seconds,
            sequence_reason=track.sequence_reason,
            title=track.title,
            audio_uri=track.audio_uri,
            audio_checksum=track.audio_checksum,
            created_at=track.created_at,
        )
    )


def list_playlist_tracks(session: Session, session_id: str) -> list[PlaylistTrack]:
    rows = (
        session.execute(
            playlist_tracks_table.select()
            .where(playlist_tracks_table.c.playlist_session_id == session_id)
            .order_by(playlist_tracks_table.c.position.asc())
        )
        .mappings()
        .all()
    )
    return [
        PlaylistTrack(
            id=r["id"],
            playlist_session_id=r["playlist_session_id"],
            song_id=r["song_id"],
            source_artifact_id=r["source_artifact_id"],
            position=r["position"],
            start_offset_seconds=r["start_offset_seconds"],
            end_offset_seconds=r["end_offset_seconds"],
            transition_type=TransitionType(r["transition_type"]),
            transition_duration_seconds=r["transition_duration_seconds"],
            sequence_reason=r["sequence_reason"],
            title=r["title"],
            audio_uri=r["audio_uri"],
            audio_checksum=r["audio_checksum"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# 3. Visual Sessions
def insert_visual_session(session: Session, vs: VisualSession) -> None:
    session.execute(
        visual_sessions_table.insert().values(
            id=vs.id,
            playlist_session_id=vs.playlist_session_id,
            brand_id=vs.brand_id,
            brand_version=vs.brand_version,
            visual_profile=vs.visual_profile,
            visual_profile_version=vs.visual_profile_version,
            visual_plan_json=json.dumps(vs.visual_plan_json, sort_keys=True, default=str),
            status=vs.status,
            created_at=vs.created_at,
            updated_at=vs.updated_at,
        )
    )


def get_visual_session(session: Session, vs_id: str) -> VisualSession | None:
    row = (
        session.execute(visual_sessions_table.select().where(visual_sessions_table.c.id == vs_id))
        .mappings()
        .first()
    )
    if not row:
        return None
    assets = list_visual_assets(session, vs_id)
    return VisualSession(
        id=row["id"],
        playlist_session_id=row["playlist_session_id"],
        brand_id=row["brand_id"],
        brand_version=row["brand_version"],
        visual_profile=row["visual_profile"],
        visual_profile_version=row["visual_profile_version"],
        visual_plan_json=json.loads(row["visual_plan_json"]),
        status=row["status"],
        assets=assets,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# 4. Visual Assets
def insert_visual_asset(session: Session, asset: VisualAsset) -> None:
    session.execute(
        visual_assets_table.insert().values(
            id=asset.id,
            visual_session_id=asset.visual_session_id,
            asset_type=asset.asset_type.value,
            source_type=asset.source_type,
            provider=asset.provider,
            provider_asset_id=asset.provider_asset_id,
            uri=asset.uri,
            checksum=asset.checksum,
            width=asset.width,
            height=asset.height,
            duration_seconds=asset.duration_seconds,
            fps=asset.fps,
            license_status=asset.license_status,
            provenance_id=asset.provenance_id,
            created_at=asset.created_at,
        )
    )


def list_visual_assets(session: Session, vs_id: str) -> list[VisualAsset]:
    rows = (
        session.execute(
            visual_assets_table.select()
            .where(visual_assets_table.c.visual_session_id == vs_id)
            .order_by(visual_assets_table.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    return [
        VisualAsset(
            id=r["id"],
            visual_session_id=r["visual_session_id"],
            asset_type=VisualAssetType(r["asset_type"]),
            source_type=r["source_type"],
            provider=r["provider"],
            provider_asset_id=r["provider_asset_id"],
            uri=r["uri"],
            checksum=r["checksum"],
            width=r["width"],
            height=r["height"],
            duration_seconds=r["duration_seconds"],
            fps=r["fps"],
            license_status=r["license_status"],
            provenance_id=r["provenance_id"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# 5. Thumbnail Variants
def insert_thumbnail_variant(session: Session, thumb: ThumbnailVariant) -> None:
    session.execute(
        thumbnail_variants_table.insert().values(
            id=thumb.id,
            media_package_id=thumb.media_package_id,
            variant_key=thumb.variant_key,
            source_assets_json=json.dumps(thumb.source_assets),
            layout_version=thumb.layout_version,
            uri=thumb.uri,
            checksum=thumb.checksum,
            width=thumb.width,
            height=thumb.height,
            text_payload=thumb.text_payload,
            predicted_ctr_score=thumb.predicted_ctr_score,
            status=thumb.status,
            created_at=thumb.created_at,
        )
    )


def list_thumbnail_variants(session: Session, package_id: str) -> list[ThumbnailVariant]:
    rows = (
        session.execute(
            thumbnail_variants_table.select()
            .where(thumbnail_variants_table.c.media_package_id == package_id)
            .order_by(thumbnail_variants_table.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    return [
        ThumbnailVariant(
            id=r["id"],
            media_package_id=r["media_package_id"],
            variant_key=r["variant_key"],
            source_assets=json.loads(r["source_assets_json"]),
            layout_version=r["layout_version"],
            uri=r["uri"],
            checksum=r["checksum"],
            width=r["width"],
            height=r["height"],
            text_payload=r["text_payload"],
            predicted_ctr_score=r["predicted_ctr_score"],
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# 6. Render Jobs
def insert_render_job(session: Session, job: RenderJob) -> None:
    session.execute(
        render_jobs_table.insert().values(
            id=job.id,
            media_package_id=job.media_package_id,
            render_profile=job.render_profile,
            render_version=job.render_version,
            job_status=job.job_status.value,
            priority=job.priority,
            attempt_count=job.attempt_count,
            worker_id=job.worker_id,
            started_at=job.started_at,
            finished_at=job.finished_at,
            input_manifest_hash=job.input_manifest_hash,
            output_checksum=job.output_checksum,
            error_code=job.error_code,
            error_detail=job.error_detail,
            created_at=job.created_at,
        )
    )


def get_render_job(session: Session, job_id: str) -> RenderJob | None:
    row = (
        session.execute(render_jobs_table.select().where(render_jobs_table.c.id == job_id))
        .mappings()
        .first()
    )
    if not row:
        return None
    return RenderJob(
        id=row["id"],
        media_package_id=row["media_package_id"],
        render_profile=row["render_profile"],
        render_version=row["render_version"],
        job_status=RenderJobStatus(row["job_status"]),
        priority=row["priority"],
        attempt_count=row["attempt_count"],
        worker_id=row["worker_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        input_manifest_hash=row["input_manifest_hash"],
        output_checksum=row["output_checksum"],
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        created_at=row["created_at"],
    )


def update_render_job_status(
    session: Session,
    job_id: str,
    status: RenderJobStatus,
    output_checksum: str | None = None,
    finished_at: datetime | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    values: dict[str, Any] = {"job_status": status.value}
    if output_checksum:
        values["output_checksum"] = output_checksum
    if finished_at:
        values["finished_at"] = finished_at
    if error_code:
        values["error_code"] = error_code
    if error_detail:
        values["error_detail"] = error_detail

    session.execute(
        render_jobs_table.update().where(render_jobs_table.c.id == job_id).values(**values)
    )


# 7. Media Packages
def list_catalog_albums(
    session: Session, brand_id: str, *, max_packages: int = 100,
) -> tuple[CatalogAlbum, ...]:
    """Load prior ready/published album fingerprint evidence, or hold on truncation.

    Includes all releases for a brand (caller ignores same-release render
    profiles). The limit is a safety ceiling, not an incomplete silent sample.
    """
    if not brand_id or max_packages < 1:
        raise ValueError("brand_id and positive max_packages required")
    statement = (
        select(
            media_packages_table.c.media_package_id,
            media_packages_table.c.release_manifest_id,
            media_packages_table.c.brand_id,
            media_packages_table.c.experiment_id,
            media_packages_table.c.content_arm_id,
            media_packages_table.c.playlist_session_id,
            media_packages_table.c.render_report_json,
            media_packages_table.c.tracklist_json,
        )
        .where(media_packages_table.c.brand_id == brand_id)
        .where(media_packages_table.c.status.in_((
            PackageStatus.READY_FOR_PUBLISH.value, PackageStatus.PUBLISHED.value,
        )))
        .order_by(media_packages_table.c.created_at.desc(), media_packages_table.c.id.desc())
        .limit(max_packages + 1)
    )
    rows = session.execute(statement).mappings().all()
    if len(rows) > max_packages:
        raise MediaAssemblyError(
            "album_catalog_scope_incomplete",
            f"brand {brand_id!r} has more than {max_packages} recorded albums; "
            "catalog comparison scope cannot be proven complete",
            ["catalog"],
        )
    albums: list[CatalogAlbum] = []
    for row in rows:
        try:
            report = json.loads(row["render_report_json"])
            tracklist = json.loads(row["tracklist_json"])
        except (TypeError, ValueError) as exc:
            raise MediaAssemblyError(
                "album_catalog_unmeasurable",
                f"stored package {row['media_package_id']} has unreadable render evidence",
                ["catalog"],
            ) from exc
        texture = report.get("album_texture") if isinstance(report, dict) else None
        stored = texture.get("evidence") if isinstance(texture, dict) else None
        signatures = stored.get("track_signatures") if isinstance(stored, dict) else None
        # Fail closed on missing source identifiers, duplicate songs,
        # shifted positions, or signatures detached from playlist order.
        if not ordered_track_evidence_matches(tracklist, signatures):
            signatures = None
        if signatures is not None:
            # The render-time gate must not trust a stored fingerprint merely
            # because its package tracklist is well formed. Check its archived
            # P6 source records, just as the read-only inventory does.
            previous = CatalogAlbum(
                row["media_package_id"], row["release_manifest_id"], signatures,
                policy_version=stored.get("policy_version") if isinstance(stored, dict) else None,
                extractor_version=(
                    stored.get("extractor_version") if isinstance(stored, dict) else None
                ),
            )
            try:
                parsed = validate_catalog_album(previous, policy=DEFAULT_ALBUM_TEXTURE_POLICY)
            except MediaAssemblyError:
                # Preserve the original evidence/version for the catalog gate
                # to report the precise corruption/version refusal.
                pass
            else:
                if not archived_sources_match(session, row, tracklist, parsed):
                    signatures = None
        albums.append(CatalogAlbum(
            media_package_id=row["media_package_id"],
            release_manifest_id=row["release_manifest_id"],
            track_signatures=signatures,
            policy_version=stored.get("policy_version") if isinstance(stored, dict) else None,
            extractor_version=(
                stored.get("extractor_version") if isinstance(stored, dict) else None
            ),
        ))
    return tuple(albums)


def insert_media_package(session: Session, pkg: MediaPackage) -> None:
    session.execute(
        media_packages_table.insert().values(
            id=pkg.media_package_id,
            media_package_id=pkg.media_package_id,
            release_manifest_id=pkg.release_manifest_id,
            experiment_id=pkg.experiment_id,
            content_arm_id=pkg.content_arm_id,
            lineage_json=pkg.lineage.model_dump(mode="json") if pkg.lineage else None,
            brand_id=pkg.brand_id,
            playlist_session_id=pkg.playlist_session_id,
            visual_session_id=pkg.visual_session_id,
            video_master_uri=pkg.video_master_uri,
            video_checksum=pkg.video_checksum,
            video_duration_seconds=pkg.video_duration_seconds,
            render_profile=pkg.render_profile,
            audio_master_uri=pkg.audio_master_uri,
            audio_checksum=pkg.audio_checksum,
            thumbnails_json=json.dumps(pkg.thumbnails),
            tracklist_json=json.dumps(pkg.tracklist),
            chapters_json=json.dumps(pkg.chapters),
            metadata_candidates_json=json.dumps(pkg.metadata_candidates),
            short_source_assets_json=json.dumps(pkg.short_source_assets),
            render_report_json=json.dumps(pkg.render_report),
            status=pkg.status.value,
            package_version=pkg.package_version,
            manifest_uri=pkg.manifest_uri,
            manifest_hash=pkg.manifest_hash,
            created_at=pkg.created_at,
            updated_at=pkg.updated_at,
        )
    )


def get_media_package_by_id(session: Session, package_id: str) -> MediaPackage | None:
    row = (
        session.execute(
            media_packages_table.select().where(
                media_packages_table.c.media_package_id == package_id
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return None
    return MediaPackage(
        media_package_id=row["media_package_id"],
        release_manifest_id=row["release_manifest_id"],
        experiment_id=row["experiment_id"],
        content_arm_id=row["content_arm_id"],
        lineage=(
            ExperimentLineage.model_validate(row["lineage_json"])
            if row["lineage_json"] is not None
            else None
        ),
        brand_id=row["brand_id"],
        playlist_session_id=row["playlist_session_id"],
        visual_session_id=row["visual_session_id"],
        video_master_uri=row["video_master_uri"],
        video_checksum=row["video_checksum"],
        video_duration_seconds=row["video_duration_seconds"],
        render_profile=row["render_profile"],
        audio_master_uri=row["audio_master_uri"],
        audio_checksum=row["audio_checksum"],
        thumbnails=json.loads(row["thumbnails_json"]),
        tracklist=json.loads(row["tracklist_json"]),
        chapters=json.loads(row["chapters_json"]),
        metadata_candidates=json.loads(row["metadata_candidates_json"]),
        short_source_assets=json.loads(row["short_source_assets_json"]),
        render_report=json.loads(row["render_report_json"]),
        status=PackageStatus(row["status"]),
        package_version=row["package_version"],
        manifest_uri=row["manifest_uri"],
        manifest_hash=row["manifest_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_media_package_by_release_manifest(
    session: Session, release_manifest_id: str
) -> MediaPackage | None:
    row = (
        session.execute(
            media_packages_table.select()
            .where(media_packages_table.c.release_manifest_id == release_manifest_id)
            .order_by(media_packages_table.c.created_at.desc())
        )
        .mappings()
        .first()
    )
    if not row:
        return None
    return MediaPackage(
        media_package_id=row["media_package_id"],
        release_manifest_id=row["release_manifest_id"],
        experiment_id=row["experiment_id"],
        content_arm_id=row["content_arm_id"],
        lineage=(
            ExperimentLineage.model_validate(row["lineage_json"])
            if row["lineage_json"] is not None
            else None
        ),
        brand_id=row["brand_id"],
        playlist_session_id=row["playlist_session_id"],
        visual_session_id=row["visual_session_id"],
        video_master_uri=row["video_master_uri"],
        video_checksum=row["video_checksum"],
        video_duration_seconds=row["video_duration_seconds"],
        render_profile=row["render_profile"],
        audio_master_uri=row["audio_master_uri"],
        audio_checksum=row["audio_checksum"],
        thumbnails=json.loads(row["thumbnails_json"]),
        tracklist=json.loads(row["tracklist_json"]),
        chapters=json.loads(row["chapters_json"]),
        metadata_candidates=json.loads(row["metadata_candidates_json"]),
        short_source_assets=json.loads(row["short_source_assets_json"]),
        render_report=json.loads(row["render_report_json"]),
        status=PackageStatus(row["status"]),
        package_version=row["package_version"],
        manifest_uri=row["manifest_uri"],
        manifest_hash=row["manifest_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def update_media_package_status(
    session: Session,
    package_id: str,
    status: PackageStatus,
    manifest_uri: str | None = None,
    *,
    now: datetime,
) -> None:
    values: dict[str, Any] = {
        "status": status.value,
        "updated_at": now,
    }
    if manifest_uri:
        values["manifest_uri"] = manifest_uri

    session.execute(
        media_packages_table.update()
        .where(media_packages_table.c.media_package_id == package_id)
        .values(**values)
    )

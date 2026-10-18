"""Repository layer for Phase P7 Autonomous YouTube Publishing Engine (P7 §8, §11, Ruling R9)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..domain.enums import OperatingMode, PrivacyStatus, PublishState
from ..domain.models import (
    ChannelProfile,
    ManifestTrack,
    PublishAttempt,
    PublishManifest,
    YouTubePublicationRecord,
)
from .models import (
    ChannelProfileRow,
    PublicationRecordRow,
    PublishAttemptRow,
    PublishManifestRow,
)


def _utc(value: datetime) -> datetime:
    """SQLite drops ``tzinfo`` on read-back; P7 stores aware-UTC instants only."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _utc_or_none(value: datetime | None) -> datetime | None:
    return None if value is None else _utc(value)

def _to_channel_domain(row: ChannelProfileRow) -> ChannelProfile:
    return ChannelProfile(
        id=row.id,
        channel_id=row.channel_id,
        channel_name=row.channel_name,
        brand_id=row.brand_id,
        oauth_credential_ref=row.oauth_credential_ref,
        default_language=row.default_language,
        default_country=row.default_country,
        default_category_id=row.default_category_id,
        default_privacy_mode=PrivacyStatus(row.default_privacy_mode),
        default_made_for_kids=row.default_made_for_kids,
        default_contains_synthetic_media=row.default_contains_synthetic_media,
        default_publish_timezone=row.default_publish_timezone,
        active=row.active,
        operating_mode=OperatingMode(row.operating_mode),
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def insert_channel_profile(session: Session, profile: ChannelProfile) -> None:
    row = ChannelProfileRow(
        id=profile.id,
        channel_id=profile.channel_id,
        channel_name=profile.channel_name,
        brand_id=profile.brand_id,
        oauth_credential_ref=profile.oauth_credential_ref,
        default_language=profile.default_language,
        default_country=profile.default_country,
        default_category_id=profile.default_category_id,
        default_privacy_mode=profile.default_privacy_mode.value,
        default_made_for_kids=profile.default_made_for_kids,
        default_contains_synthetic_media=profile.default_contains_synthetic_media,
        default_publish_timezone=profile.default_publish_timezone,
        active=profile.active,
        operating_mode=profile.operating_mode.value,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
    session.add(row)


def replace_channel_profile(session: Session, profile: ChannelProfile) -> None:
    """Overwrite the operator-set facts of an existing profile (``PUT``); the row's id,
    channel and creation instant are kept."""
    session.execute(
        update(ChannelProfileRow)
        .where(ChannelProfileRow.channel_id == profile.channel_id)
        .values(
            channel_name=profile.channel_name,
            brand_id=profile.brand_id,
            oauth_credential_ref=profile.oauth_credential_ref,
            default_language=profile.default_language,
            default_country=profile.default_country,
            default_category_id=profile.default_category_id,
            default_privacy_mode=profile.default_privacy_mode.value,
            default_made_for_kids=profile.default_made_for_kids,
            default_contains_synthetic_media=profile.default_contains_synthetic_media,
            default_publish_timezone=profile.default_publish_timezone,
            active=profile.active,
            operating_mode=profile.operating_mode.value,
            updated_at=profile.updated_at,
        )
    )


def get_channel_profile_by_id(session: Session, profile_id: str) -> ChannelProfile | None:
    row = session.execute(
        select(ChannelProfileRow).where(ChannelProfileRow.id == profile_id)
    ).scalar_one_or_none()
    return _to_channel_domain(row) if row else None


def get_channel_profile_by_channel_id(session: Session, channel_id: str) -> ChannelProfile | None:
    row = session.execute(
        select(ChannelProfileRow).where(ChannelProfileRow.channel_id == channel_id)
    ).scalar_one_or_none()
    return _to_channel_domain(row) if row else None


def _to_manifest_domain(row: PublishManifestRow) -> PublishManifest:
    return PublishManifest(
        manifest_id=row.manifest_id,
        channel_id=row.channel_id,
        media_package_id=row.media_package_id,
        release_manifest_id=row.release_manifest_id,
        experiment_id=row.experiment_id,
        experiment_family_id=row.experiment_family_id,
        content_arm_id=row.content_arm_id,
        content_genome_id=row.content_genome_id,
        production_directive_id=row.production_directive_id,
        brand_id=row.brand_id,
        idempotency_key=row.idempotency_key,
        video_master_uri=row.video_master_uri,
        video_checksum=row.video_checksum,
        video_duration_seconds=row.video_duration_seconds,
        thumbnail_uri=row.thumbnail_uri,
        thumbnail_checksum=row.thumbnail_checksum,
        title=row.title,
        description=row.description,
        tags=list(row.tags) if isinstance(row.tags, list) else [],
        category_id=row.category_id,
        privacy_status=PrivacyStatus(row.privacy_status),
        publish_at=_utc_or_none(row.publish_at),
        contains_synthetic_media=row.contains_synthetic_media,
        made_for_kids=row.made_for_kids,
        playlist_id=row.playlist_id,
        playlist_title=row.playlist_title,
        metadata_version=row.metadata_version,
        tracks=tuple(ManifestTrack(**t) for t in (row.tracks_json or [])),
        created_at=_utc(row.created_at),
    )


def insert_publish_manifest(session: Session, manifest: PublishManifest) -> None:
    row = PublishManifestRow(
        manifest_id=manifest.manifest_id,
        channel_id=manifest.channel_id,
        media_package_id=manifest.media_package_id,
        release_manifest_id=manifest.release_manifest_id,
        experiment_id=manifest.experiment_id,
        experiment_family_id=manifest.experiment_family_id,
        content_arm_id=manifest.content_arm_id,
        content_genome_id=manifest.content_genome_id,
        production_directive_id=manifest.production_directive_id,
        brand_id=manifest.brand_id,
        idempotency_key=manifest.idempotency_key,
        video_master_uri=manifest.video_master_uri,
        video_checksum=manifest.video_checksum,
        video_duration_seconds=manifest.video_duration_seconds,
        thumbnail_uri=manifest.thumbnail_uri,
        thumbnail_checksum=manifest.thumbnail_checksum,
        title=manifest.title,
        description=manifest.description,
        tags=manifest.tags,
        category_id=manifest.category_id,
        privacy_status=manifest.privacy_status.value,
        publish_at=manifest.publish_at,
        contains_synthetic_media=manifest.contains_synthetic_media,
        made_for_kids=manifest.made_for_kids,
        playlist_id=manifest.playlist_id,
        playlist_title=manifest.playlist_title,
        metadata_version=manifest.metadata_version,
        tracks_json=[t.model_dump() for t in manifest.tracks],
        created_at=manifest.created_at,
    )
    session.add(row)


def get_publish_manifest_by_id(session: Session, manifest_id: str) -> PublishManifest | None:
    row = session.execute(
        select(PublishManifestRow).where(PublishManifestRow.manifest_id == manifest_id)
    ).scalar_one_or_none()
    return _to_manifest_domain(row) if row else None


def get_publish_manifest_by_idempotency_key(
    session: Session, idempotency_key: str
) -> PublishManifest | None:
    row = session.execute(
        select(PublishManifestRow).where(PublishManifestRow.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    return _to_manifest_domain(row) if row else None


def _to_attempt_domain(row: PublishAttemptRow) -> PublishAttempt:
    return PublishAttempt(
        attempt_id=row.attempt_id,
        manifest_id=row.manifest_id,
        channel_id=row.channel_id,
        idempotency_key=row.idempotency_key,
        state=PublishState(row.state),
        operating_mode=OperatingMode(row.operating_mode),
        upload_session_uri=row.upload_session_uri,
        youtube_video_id=row.youtube_video_id,
        youtube_playlist_id=row.youtube_playlist_id,
        youtube_playlist_item_id=row.youtube_playlist_item_id,
        thumbnail_uploaded=row.thumbnail_uploaded,
        bytes_uploaded=row.bytes_uploaded,
        total_bytes=row.total_bytes,
        error_code=row.error_code,
        error_message=row.error_message,
        retry_count=row.retry_count,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        completed_at=_utc_or_none(row.completed_at),
    )


def insert_publish_attempt(session: Session, attempt: PublishAttempt) -> None:
    row = PublishAttemptRow(
        attempt_id=attempt.attempt_id,
        manifest_id=attempt.manifest_id,
        channel_id=attempt.channel_id,
        idempotency_key=attempt.idempotency_key,
        state=attempt.state.value,
        operating_mode=attempt.operating_mode.value,
        upload_session_uri=attempt.upload_session_uri,
        youtube_video_id=attempt.youtube_video_id,
        youtube_playlist_id=attempt.youtube_playlist_id,
        youtube_playlist_item_id=attempt.youtube_playlist_item_id,
        thumbnail_uploaded=attempt.thumbnail_uploaded,
        bytes_uploaded=attempt.bytes_uploaded,
        total_bytes=attempt.total_bytes,
        error_code=attempt.error_code,
        error_message=attempt.error_message,
        retry_count=attempt.retry_count,
        created_at=attempt.created_at,
        updated_at=attempt.updated_at,
        completed_at=attempt.completed_at,
    )
    session.add(row)


def update_publish_attempt(
    session: Session,
    attempt_id: str,
    *,
    state: PublishState | None = None,
    upload_session_uri: str | None = None,
    youtube_video_id: str | None = None,
    youtube_playlist_id: str | None = None,
    youtube_playlist_item_id: str | None = None,
    thumbnail_uploaded: bool | None = None,
    bytes_uploaded: int | None = None,
    total_bytes: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retry_count: int | None = None,
    updated_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {}
    if state is not None:
        values["state"] = state.value
    if upload_session_uri is not None:
        values["upload_session_uri"] = upload_session_uri
    if youtube_video_id is not None:
        values["youtube_video_id"] = youtube_video_id
    if youtube_playlist_id is not None:
        values["youtube_playlist_id"] = youtube_playlist_id
    if youtube_playlist_item_id is not None:
        values["youtube_playlist_item_id"] = youtube_playlist_item_id
    if thumbnail_uploaded is not None:
        values["thumbnail_uploaded"] = thumbnail_uploaded
    if bytes_uploaded is not None:
        values["bytes_uploaded"] = bytes_uploaded
    if total_bytes is not None:
        values["total_bytes"] = total_bytes
    if error_code is not None:
        values["error_code"] = error_code
    if error_message is not None:
        values["error_message"] = error_message
    if retry_count is not None:
        values["retry_count"] = retry_count
    if updated_at is not None:
        values["updated_at"] = updated_at
    if completed_at is not None:
        values["completed_at"] = completed_at

    if values:
        session.execute(
            update(PublishAttemptRow)
            .where(PublishAttemptRow.attempt_id == attempt_id)
            .values(**values)
        )


def get_publish_attempt_by_id(session: Session, attempt_id: str) -> PublishAttempt | None:
    row = session.execute(
        select(PublishAttemptRow).where(PublishAttemptRow.attempt_id == attempt_id)
    ).scalar_one_or_none()
    return _to_attempt_domain(row) if row else None


def get_publish_attempt_by_idempotency_key(
    session: Session, idempotency_key: str
) -> PublishAttempt | None:
    row = session.execute(
        select(PublishAttemptRow)
        .where(PublishAttemptRow.idempotency_key == idempotency_key)
        .order_by(PublishAttemptRow.created_at.desc())
    ).scalars().first()
    return _to_attempt_domain(row) if row else None


def _to_record_domain(row: PublicationRecordRow) -> YouTubePublicationRecord:
    return YouTubePublicationRecord(
        publication_record_id=row.publication_record_id,
        youtube_video_id=row.youtube_video_id,
        channel_id=row.channel_id,
        publish_attempt_id=row.publish_attempt_id,
        media_package_id=row.media_package_id,
        release_manifest_id=row.release_manifest_id,
        experiment_id=row.experiment_id,
        experiment_family_id=row.experiment_family_id,
        content_arm_id=row.content_arm_id,
        content_genome_id=row.content_genome_id,
        production_directive_id=row.production_directive_id,
        title=row.title,
        privacy_status=PrivacyStatus(row.privacy_status),
        contains_synthetic_media=row.contains_synthetic_media,
        published_at=_utc(row.published_at),
        video_url=row.video_url,
        thumbnail_url=row.thumbnail_url,
        playlist_id=row.playlist_id,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def insert_publication_record(session: Session, record: YouTubePublicationRecord) -> None:
    row = PublicationRecordRow(
        publication_record_id=record.publication_record_id,
        youtube_video_id=record.youtube_video_id,
        channel_id=record.channel_id,
        publish_attempt_id=record.publish_attempt_id,
        media_package_id=record.media_package_id,
        release_manifest_id=record.release_manifest_id,
        experiment_id=record.experiment_id,
        experiment_family_id=record.experiment_family_id,
        content_arm_id=record.content_arm_id,
        content_genome_id=record.content_genome_id,
        production_directive_id=record.production_directive_id,
        title=record.title,
        privacy_status=record.privacy_status.value,
        contains_synthetic_media=record.contains_synthetic_media,
        published_at=record.published_at,
        video_url=record.video_url,
        thumbnail_url=record.thumbnail_url,
        playlist_id=record.playlist_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
    session.add(row)


def get_publication_record_by_video_id(
    session: Session, youtube_video_id: str
) -> YouTubePublicationRecord | None:
    row = session.execute(
        select(PublicationRecordRow).where(PublicationRecordRow.youtube_video_id == youtube_video_id)
    ).scalar_one_or_none()
    return _to_record_domain(row) if row else None


def get_publication_record_by_package_id(
    session: Session, media_package_id: str
) -> YouTubePublicationRecord | None:
    row = session.execute(
        select(PublicationRecordRow).where(PublicationRecordRow.media_package_id == media_package_id)
    ).scalar_one_or_none()
    return _to_record_domain(row) if row else None

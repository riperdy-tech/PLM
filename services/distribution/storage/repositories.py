"""Repositories for distribution persistence (P8 §6, §7, §8, Ruling R9, R10)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..domain.enums import (
    ArtistType,
    DeliveryStatus,
    ISRCSource,
    ReleaseStatus,
    ReleaseType,
    StorePlatform,
)
from ..domain.models import (
    ArtistProfile,
    DeliveryTarget,
    DistributionRelease,
    DistributionReportReference,
    DistributionTrack,
)
from .models import ArtistRow, ProviderReportRow, ReleaseRow, TrackRow


def insert_artist(session: Session, artist: ArtistProfile) -> None:
    row = ArtistRow(
        id=artist.id,
        canonical_name=artist.canonical_name,
        normalized_name=artist.normalized_name,
        artist_type=artist.artist_type.value,
        brand_id=artist.brand_id,
        spotify_artist_id=artist.spotify_artist_id,
        apple_artist_id=artist.apple_artist_id,
        created_at=artist.created_at,
        updated_at=artist.updated_at,
    )
    session.add(row)


def get_artist_by_id(session: Session, artist_id: str) -> ArtistProfile | None:
    row = session.execute(
        select(ArtistRow).where(ArtistRow.id == artist_id)
    ).scalar_one_or_none()
    if not row:
        return None
    return ArtistProfile(
        id=row.id,
        canonical_name=row.canonical_name,
        normalized_name=row.normalized_name,
        artist_type=ArtistType(row.artist_type),
        brand_id=row.brand_id,
        spotify_artist_id=row.spotify_artist_id,
        apple_artist_id=row.apple_artist_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def get_artist_by_name(session: Session, name: str) -> ArtistProfile | None:
    norm = name.strip().lower()
    row = session.execute(
        select(ArtistRow).where(ArtistRow.normalized_name == norm)
    ).scalar_one_or_none()
    if not row:
        return None
    return ArtistProfile(
        id=row.id,
        canonical_name=row.canonical_name,
        normalized_name=row.normalized_name,
        artist_type=ArtistType(row.artist_type),
        brand_id=row.brand_id,
        spotify_artist_id=row.spotify_artist_id,
        apple_artist_id=row.apple_artist_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def insert_release(
    session: Session,
    release: DistributionRelease,
    *,
    source_manifest_ids: list[str] | None = None,
    experiment_id: str = "exp_unknown",
    experiment_family_id: str = "fam_unknown",
    content_arm_id: str = "arm_unknown",
    content_genome_id: str = "gen_unknown",
    production_directive_id: str = "dir_unknown",
    media_package_id: str = "pkg_unknown",
) -> None:
    deliv_dicts = [
        {
            "store": d.store.value,
            "external_release_id": d.external_release_id,
            "status": d.status.value,
            "first_delivered_at": d.first_delivered_at.isoformat() if d.first_delivered_at else None,
        }
        for d in release.deliveries
    ]
    row = ReleaseRow(
        id=release.id,
        release_id=release.release_id,
        artist_id=release.artist_id,
        release_type=release.release_type.value,
        release_title=release.release_title,
        release_version=release.release_version,
        status=release.status.value,
        provider=release.provider,
        provider_release_id=release.provider_release_id,
        submission_fingerprint=release.submission_fingerprint,
        deliveries=deliv_dicts,
        source_manifest_ids=source_manifest_ids or [],
        experiment_id=experiment_id,
        experiment_family_id=experiment_family_id,
        content_arm_id=content_arm_id,
        content_genome_id=content_genome_id,
        production_directive_id=production_directive_id,
        media_package_id=media_package_id,
        created_at=release.created_at,
        updated_at=release.updated_at,
    )
    session.add(row)


def _to_release_domain(row: ReleaseRow) -> DistributionRelease:
    deliveries: list[DeliveryTarget] = []
    if isinstance(row.deliveries, list):
        for d in row.deliveries:
            first_del = None
            if d.get("first_delivered_at"):
                first_del = datetime.fromisoformat(d["first_delivered_at"])
            deliveries.append(
                DeliveryTarget(
                    store=StorePlatform(d["store"]),
                    external_release_id=d.get("external_release_id"),
                    status=DeliveryStatus(d["status"]),
                    first_delivered_at=first_del,
                )
            )
    return DistributionRelease(
        id=row.id,
        release_id=row.release_id,
        artist_id=row.artist_id,
        release_type=ReleaseType(row.release_type),
        release_title=row.release_title,
        release_version=row.release_version,
        status=ReleaseStatus(row.status),
        provider=row.provider,
        provider_release_id=row.provider_release_id,
        submission_fingerprint=row.submission_fingerprint,
        deliveries=deliveries,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def get_release_by_id(session: Session, release_id: str) -> DistributionRelease | None:
    row = session.execute(
        select(ReleaseRow).where(ReleaseRow.release_id == release_id)
    ).scalar_one_or_none()
    return _to_release_domain(row) if row else None


def get_release_by_fingerprint(session: Session, fingerprint: str) -> DistributionRelease | None:
    row = session.execute(
        select(ReleaseRow).where(ReleaseRow.submission_fingerprint == fingerprint)
    ).scalar_one_or_none()
    return _to_release_domain(row) if row else None


def update_release_status(
    session: Session,
    release_id: str,
    *,
    status: ReleaseStatus | None = None,
    provider_release_id: str | None = None,
    deliveries: list[DeliveryTarget] | None = None,
    updated_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {}
    if status is not None:
        values["status"] = status.value
    if provider_release_id is not None:
        values["provider_release_id"] = provider_release_id
    if deliveries is not None:
        values["deliveries"] = [
            {
                "store": d.store.value,
                "external_release_id": d.external_release_id,
                "status": d.status.value,
                "first_delivered_at": d.first_delivered_at.isoformat() if d.first_delivered_at else None,
            }
            for d in deliveries
        ]
    if updated_at is not None:
        values["updated_at"] = updated_at

    if values:
        session.execute(
            update(ReleaseRow).where(ReleaseRow.release_id == release_id).values(**values)
        )


def insert_track(session: Session, track: DistributionTrack) -> None:
    row = TrackRow(
        id=track.id,
        track_id=track.track_id,
        release_id=track.release_id,
        track_number=track.track_number,
        disc_number=track.disc_number,
        title=track.title,
        version_title=track.version_title,
        isrc=track.isrc,
        isrc_source=track.isrc_source.value,
        duration_seconds=track.duration_seconds,
        audio_asset_uri=track.audio_asset_uri,
        audio_checksum=track.audio_checksum,
        explicit=track.explicit,
        created_at=track.created_at,
    )
    session.add(row)


def get_tracks_by_release_id(session: Session, release_id: str) -> list[DistributionTrack]:
    rows = session.execute(
        select(TrackRow).where(TrackRow.release_id == release_id).order_by(TrackRow.track_number)
    ).scalars().all()
    return [
        DistributionTrack(
            id=r.id,
            track_id=r.track_id,
            release_id=r.release_id,
            track_number=r.track_number,
            disc_number=r.disc_number,
            title=r.title,
            version_title=r.version_title,
            isrc=r.isrc,
            isrc_source=ISRCSource(r.isrc_source),
            duration_seconds=r.duration_seconds,
            audio_asset_uri=r.audio_asset_uri,
            audio_checksum=r.audio_checksum,
            explicit=r.explicit,
            created_at=r.created_at,
        )
        for r in rows
    ]


def get_track_by_isrc(session: Session, isrc: str) -> DistributionTrack | None:
    row = session.execute(
        select(TrackRow).where(TrackRow.isrc == isrc)
    ).scalar_one_or_none()
    if not row:
        return None
    return DistributionTrack(
        id=row.id,
        track_id=row.track_id,
        release_id=row.release_id,
        track_number=row.track_number,
        disc_number=row.disc_number,
        title=row.title,
        version_title=row.version_title,
        isrc=row.isrc,
        isrc_source=ISRCSource(row.isrc_source),
        duration_seconds=row.duration_seconds,
        audio_asset_uri=row.audio_asset_uri,
        audio_checksum=row.audio_checksum,
        explicit=row.explicit,
        created_at=row.created_at,
    )


def insert_provider_report(session: Session, report: DistributionReportReference) -> None:
    row = ProviderReportRow(
        id=f"rep_{report.report_id}",
        report_id=report.report_id,
        provider=report.provider,
        provider_period=report.provider_period,
        received_at=report.received_at,
        report_checksum=report.report_checksum,
        source_reference=report.source_reference,
        raw_payload_uri=report.raw_payload_uri,
        report_type=report.report_type,
        created_at=report.received_at,
    )
    session.add(row)


def get_provider_report_by_id(session: Session, report_id: str) -> DistributionReportReference | None:
    row = session.execute(
        select(ProviderReportRow).where(ProviderReportRow.report_id == report_id)
    ).scalar_one_or_none()
    if not row:
        return None
    return DistributionReportReference(
        report_id=row.report_id,
        provider=row.provider,
        provider_period=row.provider_period,
        received_at=row.received_at,
        report_checksum=row.report_checksum,
        source_reference=row.source_reference,
        raw_payload_uri=row.raw_payload_uri,
        report_type=row.report_type,
    )

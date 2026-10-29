"""Bounded, read-only P6 texture inventory for explicitly selected brands.

This reports stored *metadata shape*, not audio/rights verification or approval.
No artifact store is consulted and no package state is changed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from .domain.enums import PackageStatus
from .errors import MediaAssemblyError
from .playlist.catalog import (
    CatalogAlbum,
    ordered_track_evidence_matches,
    validate_catalog_album,
)
from .playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY
from .storage.models import media_packages_table
from .storage.source_evidence import archived_sources_match

MAX_INVENTORY_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class TextureInventoryEntry:
    package_id: str
    release_manifest_id: str
    classification: str
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class TextureInventoryPage:
    brand_id: str
    entries: tuple[TextureInventoryEntry, ...]
    has_more: bool
    next_after_id: str | None


def _classify(session: Session, row: RowMapping) -> TextureInventoryEntry:
    package_id: str = row["media_package_id"]
    release_id: str = row["release_manifest_id"]
    try:
        report = json.loads(row["render_report_json"])
        tracklist = json.loads(row["tracklist_json"])
    except (TypeError, ValueError):
        return TextureInventoryEntry(
            package_id, release_id, "unreadable_record", "album_catalog_unmeasurable",
        )

    if not isinstance(report, dict) or not isinstance(tracklist, list) or not tracklist:
        return TextureInventoryEntry(
            package_id, release_id, "invalid_evidence", "album_catalog_unmeasurable",
        )
    texture = report.get("album_texture")
    if texture is None:
        return TextureInventoryEntry(
            package_id, release_id, "missing_evidence", "album_catalog_unmeasurable",
        )
    if not isinstance(texture, dict):
        return TextureInventoryEntry(
            package_id, release_id, "invalid_evidence", "album_catalog_unmeasurable",
        )
    evidence = texture.get("evidence")
    if evidence is None:
        return TextureInventoryEntry(
            package_id, release_id, "missing_evidence", "album_catalog_unmeasurable",
        )
    if not isinstance(evidence, dict):
        return TextureInventoryEntry(
            package_id, release_id, "invalid_evidence", "album_catalog_unmeasurable",
        )
    prior = CatalogAlbum(
        package_id, release_id, evidence.get("track_signatures"),
        policy_version=evidence.get("policy_version"),
        extractor_version=evidence.get("extractor_version"),
    )
    try:
        validated = validate_catalog_album(prior, policy=DEFAULT_ALBUM_TEXTURE_POLICY)
    except MediaAssemblyError as exc:
        classification = (
            "incompatible_version"
            if exc.code == "album_catalog_version_incompatible"
            else "invalid_evidence"
        )
        return TextureInventoryEntry(package_id, release_id, classification, exc.code)

    # Use the same strict sequence/identity rule as the render-time catalog.
    if not ordered_track_evidence_matches(tracklist, prior.track_signatures):
        return TextureInventoryEntry(
            package_id, release_id, "invalid_evidence", "album_catalog_unmeasurable",
        )
    if not archived_sources_match(session, row, tracklist, validated):
        return TextureInventoryEntry(
            package_id, release_id, "source_metadata_unverifiable",
            "album_catalog_source_mismatch",
        )
    return TextureInventoryEntry(package_id, release_id, "evidence_structure_valid", None)


def inspect_brand_album_textures(
    session: Session, brand_id: str, *, page_size: int = 20,
    after_package_id: str | None = None,
) -> TextureInventoryPage:
    """Return a stable-ID-ordered bounded page of READY/PUBLISHED packages only.

    Pagination is not a cross-request snapshot. A complete inspection must
    traverse all pages, and even a valid metadata shape is no proof of source
    audio checksum or commercial rights.
    """
    if not isinstance(brand_id, str) or not brand_id.strip():
        raise ValueError("explicit nonempty brand_id is required")
    if not 1 <= page_size <= MAX_INVENTORY_PAGE_SIZE:
        raise ValueError(f"page_size must be 1..{MAX_INVENTORY_PAGE_SIZE}")
    if after_package_id is not None and (
        not isinstance(after_package_id, str) or not after_package_id.strip()
    ):
        raise ValueError("after_package_id must be nonempty when provided")

    t = media_packages_table.c
    statement = (
        sa.select(
            t.media_package_id, t.release_manifest_id, t.brand_id,
            t.experiment_id, t.content_arm_id, t.playlist_session_id,
            t.render_report_json, t.tracklist_json,
        )
        .where(t.brand_id == brand_id)
        .where(t.status.in_(
            (PackageStatus.READY_FOR_PUBLISH.value, PackageStatus.PUBLISHED.value)
        ))
        .order_by(t.media_package_id.asc())
        .limit(page_size + 1)
    )
    if after_package_id is not None:
        statement = statement.where(t.media_package_id > after_package_id)
    rows = session.execute(statement).mappings().all()
    more = len(rows) > page_size
    selected = rows[:page_size]
    return TextureInventoryPage(
        brand_id=brand_id,
        entries=tuple(_classify(session, row) for row in selected),
        has_more=more,
        next_after_id=selected[-1]["media_package_id"] if more else None,
    )

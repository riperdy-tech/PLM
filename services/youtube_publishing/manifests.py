"""Build P7's publish manifest from a Seam C6 hop-1 package (P7 §9, §10, ADR-0029).

The one place the publish manifest is derived, shared by the API and the CLI. Every value
comes from the parsed package or from the operator-configured channel profile:

* candidate assignment is deterministic and frozen: a caller may name explicit
  C6 candidate IDs, otherwise the first declared candidate is retained for compatibility;
* ``containsSyntheticMedia`` is always true: every recording this pipeline publishes
  is AI-generated (ADR-0044), whatever a package declares (ARCH-P18);
* privacy and made-for-kids are the channel's configured defaults;
* no playlist is attached unless one is configured (none is configurable yet).
"""

from __future__ import annotations

from datetime import datetime

from plm_contracts.seams.c6 import MediaPackagePayload, MetadataCandidate, ThumbnailAsset
from plm_platform.media_timing import classify_saved_media_timing

from .domain.ids import build_idempotency_key, build_manifest_id
from .domain.models import CandidateSelection, ChannelProfile, ManifestTrack, PublishManifest

__all__ = ["CandidateSelectionError", "PackageNotReadyError", "build_publish_manifest",
           "preflight_metadata", "same_publish_choice"]


class PackageNotReadyError(ValueError):
    """The package is not in a publishable state."""


class CandidateSelectionError(ValueError):
    """The requested assignment is absent, ambiguous or duplicates a candidate ID."""


def select_candidates(
    pkg: MediaPackagePayload, *,
    metadata_variant_id: str | None = None,
    thumbnail_id: str | None = None,
) -> tuple[MetadataCandidate, ThumbnailAsset, CandidateSelection]:
    """Choose only declared, uniquely named candidates; never invent a winner."""
    metadata_ids = tuple(item.variant_id for item in pkg.metadata_candidates)
    thumbnail_ids = tuple(item.id for item in pkg.thumbnails)
    if len(set(metadata_ids)) != len(metadata_ids):
        raise CandidateSelectionError("duplicate metadata candidate IDs")
    if len(set(thumbnail_ids)) != len(thumbnail_ids):
        raise CandidateSelectionError("duplicate thumbnail candidate IDs")
    chosen_metadata = metadata_variant_id if metadata_variant_id is not None else metadata_ids[0]
    chosen_thumbnail = thumbnail_id if thumbnail_id is not None else thumbnail_ids[0]
    if chosen_metadata not in metadata_ids:
        raise CandidateSelectionError(f"metadata candidate {chosen_metadata!r} not in package")
    if chosen_thumbnail not in thumbnail_ids:
        raise CandidateSelectionError(f"thumbnail candidate {chosen_thumbnail!r} not in package")
    selection = CandidateSelection(
        method="explicit" if metadata_variant_id is not None or thumbnail_id is not None
        else "first_declared",
        metadata_variant_id=chosen_metadata,
        thumbnail_id=chosen_thumbnail,
        eligible_metadata_ids=metadata_ids,
        eligible_thumbnail_ids=thumbnail_ids,
    )
    meta = next(m for m in pkg.metadata_candidates if m.variant_id == chosen_metadata)
    thumb = next(t for t in pkg.thumbnails if t.id == chosen_thumbnail)
    return meta, thumb, selection


def same_publish_choice(
    existing: PublishManifest, proposed: PublishManifest, *, explicit_requested: bool,
) -> bool:
    """Never reassign a stored publication under its existing idempotency key.

    Legacy rows have no recorded assignment. They may replay unchanged with
    the default choice, but cannot retrospectively claim an explicit exposure.
    """
    if existing.selection is None:
        if explicit_requested:
            return False
    elif existing.selection != proposed.selection:
        return False
    return existing.model_dump(exclude={"created_at", "selection"}) == (
        proposed.model_dump(exclude={"created_at", "selection"})
    )


def preflight_metadata(pkg: MediaPackagePayload, *,
                       metadata_variant_id: str | None = None) -> dict[str, object]:
    """The exact metadata candidate the manifest publishes, for preflight."""
    meta, _, _ = select_candidates(pkg, metadata_variant_id=metadata_variant_id)
    return {
        "title": meta.title,
        "description": meta.description,
        "tags": list(meta.tags),
        "category_id": meta.category_id,
    }


def build_publish_manifest(
    pkg: MediaPackagePayload,
    profile: ChannelProfile,
    *,
    publish_at: datetime | None,
    now: datetime,
    metadata_variant_id: str | None = None,
    thumbnail_id: str | None = None,
) -> PublishManifest:
    """Derive the immutable publish manifest for one package on one configured channel."""
    if pkg.status != "ready_for_publish":
        raise PackageNotReadyError(
            f"media package status is {pkg.status!r}, not 'ready_for_publish'"
        )
    timing = classify_saved_media_timing(
        pkg.status, pkg.video.duration_seconds, pkg.render_report,
    )
    if timing != "saved_stream_evidence_consistent":
        raise PackageNotReadyError(f"P6 render timing QA evidence is {timing}")
    meta, thumb, selection = select_candidates(
        pkg, metadata_variant_id=metadata_variant_id, thumbnail_id=thumbnail_id,
    )
    lineage = pkg.lineage
    channel_id = profile.channel_id
    return PublishManifest(
        manifest_id=build_manifest_id(channel_id, pkg.media_package_id, pkg.video.checksum),
        channel_id=channel_id,
        media_package_id=pkg.media_package_id,
        release_manifest_id=pkg.release_manifest_id,
        experiment_id=lineage.experiment_id,
        experiment_family_id=lineage.experiment_family_id,
        content_arm_id=lineage.content_arm_id,
        content_genome_id=lineage.content_genome_id,
        production_directive_id=lineage.production_directive_id,
        brand_id=pkg.brand_id,
        idempotency_key=build_idempotency_key(
            channel_id, pkg.media_package_id, pkg.video.checksum
        ),
        video_master_uri=pkg.video.master_uri,
        video_checksum=pkg.video.checksum,
        video_duration_seconds=pkg.video.duration_seconds,
        thumbnail_uri=thumb.uri,
        thumbnail_checksum=thumb.checksum,
        title=meta.title,
        description=meta.description,
        tags=list(meta.tags),
        category_id=meta.category_id,
        privacy_status=profile.default_privacy_mode,
        publish_at=publish_at,
        contains_synthetic_media=True,
        made_for_kids=profile.default_made_for_kids,
        playlist_id=None,
        playlist_title=None,
        metadata_version=meta.variant_id,
        selection=selection,
        tracks=tuple(
            ManifestTrack(candidate_id=t.song_id, artifact_id=t.source_artifact_id)
            for t in pkg.tracklist
        ),
        created_at=now,
    )

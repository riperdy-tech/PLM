"""Build P7's publish manifest from a Seam C6 hop-1 package (P7 §9, §10, ADR-0029).

The one place the publish manifest is derived, shared by the API and the CLI. Every value
comes from the parsed package or from the operator-configured channel profile:

* title, description, tags and category come from the first metadata candidate (P7 policy:
  publish the candidate P6 ranked first);
* ``containsSyntheticMedia`` is the package's ``ai_disclosure_required`` (ARCH-P18);
* privacy and made-for-kids are the channel's configured defaults;
* no playlist is attached unless one is configured (none is configurable yet).
"""

from __future__ import annotations

from datetime import datetime

from plm_contracts.seams.c6 import MediaPackagePayload

from .domain.ids import build_idempotency_key, build_manifest_id
from .domain.models import ChannelProfile, ManifestTrack, PublishManifest

__all__ = ["PackageNotReadyError", "build_publish_manifest", "preflight_metadata"]


class PackageNotReadyError(ValueError):
    """The package is not in a publishable state."""


def preflight_metadata(pkg: MediaPackagePayload) -> dict[str, object]:
    """The metadata the preflight gates check, from the chosen candidate."""
    meta = pkg.metadata_candidates[0]
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
) -> PublishManifest:
    """Derive the immutable publish manifest for one package on one configured channel."""
    if pkg.status != "ready_for_publish":
        raise PackageNotReadyError(
            f"media package status is {pkg.status!r}, not 'ready_for_publish'"
        )
    meta = pkg.metadata_candidates[0]
    thumb = pkg.thumbnails[0]
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
        contains_synthetic_media=pkg.ai_disclosure_required,
        made_for_kids=profile.default_made_for_kids,
        playlist_id=None,
        playlist_title=None,
        metadata_version=meta.variant_id,
        tracks=tuple(
            ManifestTrack(candidate_id=t.song_id, artifact_id=t.source_artifact_id)
            for t in pkg.tracklist
        ),
        created_at=now,
    )

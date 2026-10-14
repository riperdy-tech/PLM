"""Release compiler for Phase P8 (P8 §11, Rulings R7, R8, R9, ADR-0029).

Compiles P5's per-track clearances (Seam C5) and P6's media package (Seam C6 hop 1) into
an immutable :class:`CanonicalReleasePackage`. Both inputs are parsed seam models, and
every track fact comes from them:

* each track must be cleared for release by P5 (passed, or overridden on record);
* each track's lineage must equal the package's lineage, so the release is attributable
  to one arm;
* the package tracklist and the manifests must name the same recordings;
* duration, audio URI and checksum are P5's recorded facts; the title is P6's tracklist
  title; the ISRC comes from the persisted per-recording allocator.

Nothing is defaulted: no "Untitled Release", 180 s duration, invented URI, hashed-URI
checksum, or position-derived ISRC. A gap refuses compilation with a reason code.
Release-level metadata (title, date, label lines, territories) is supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from plm_contracts import ErrorShape
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_contracts.seams.c6 import MediaPackagePayload

from ..domain.enums import ISRCSource, ReleaseType
from ..domain.ids import build_release_id, build_submission_fingerprint, build_track_id
from ..domain.models import CanonicalReleasePackage, DistributionTrack

__all__ = ["ReleaseClearanceError", "compile_canonical_release_package"]

IsrcAllocator = Callable[[str], tuple[str, ISRCSource]]


class ReleaseClearanceError(ValueError):
    """P8 refused to compile a release; ``error`` carries the reason code (R8)."""

    def __init__(self, code: str, message: str, field_paths: list[str] | None = None) -> None:
        self.error = ErrorShape(code=code, field_paths=field_paths or [], message=message)
        super().__init__(f"{code}: {message}")


def compile_canonical_release_package(
    *,
    track_manifests: Sequence[ReleaseManifestPayload],
    media_package: MediaPackagePayload,
    artist_id: str,
    artist_name: str,
    release_date: str,
    label_name: str,
    copyright_line: str,
    publisher_line: str,
    territories: str,
    allocate_isrc: IsrcAllocator,
    now: datetime,
    release_title: str | None = None,
    release_version: int = 1,
    release_type: ReleaseType | None = None,
) -> CanonicalReleasePackage:
    """Compile cleared tracks and their media package into a canonical release."""
    # 1. P5 clearance and lineage (R7, R8, R9)
    blocked = [m.candidate_id for m in track_manifests if not m.cleared_for_release]
    if blocked:
        raise ReleaseClearanceError(
            "track_not_cleared",
            f"tracks {blocked} are not cleared by P5 and carry no audited override",
            ["track_manifests"],
        )
    foreign = [m.candidate_id for m in track_manifests if m.lineage != media_package.lineage]
    if foreign:
        raise ReleaseClearanceError(
            "lineage_mismatch",
            f"tracks {foreign} belong to a different lineage than media package "
            f"{media_package.media_package_id!r}",
            ["track_manifests"],
        )

    # 2. The tracklist and the clearances must name the same recordings.
    by_candidate = {m.candidate_id: m for m in track_manifests}
    listed = [t.song_id for t in media_package.tracklist]
    if sorted(listed) != sorted(by_candidate) or len(set(listed)) != len(listed):
        raise ReleaseClearanceError(
            "tracklist_mismatch",
            f"package tracklist {listed} does not match the cleared tracks "
            f"{sorted(by_candidate)}",
            ["media_package.tracklist", "track_manifests"],
        )

    # 3. Release identity: explicit title, else the package's first metadata candidate.
    title = release_title or media_package.metadata_candidates[0].title
    slug = title.lower().replace(" ", "_").strip()
    release_id = build_release_id(media_package.brand_id, slug)

    tracks: list[DistributionTrack] = []
    for entry in sorted(media_package.tracklist, key=lambda t: t.position):
        manifest = by_candidate[entry.song_id]
        isrc, isrc_source = allocate_isrc(manifest.candidate_id)
        track_number = len(tracks) + 1
        track_id = build_track_id(release_id, track_number)
        tracks.append(
            DistributionTrack(
                id=f"rec_{track_id}",
                track_id=track_id,
                release_id=release_id,
                track_number=track_number,
                disc_number=1,
                title=entry.title,
                version_title=None,
                isrc=isrc,
                isrc_source=isrc_source,
                duration_seconds=manifest.measured_duration_seconds,
                audio_asset_uri=manifest.artifact_uri,
                audio_checksum=manifest.artifact_sha256,
                explicit=False,
                created_at=now,
            )
        )

    if release_type is None:
        resolved_type = (
            ReleaseType.SINGLE
            if len(tracks) == 1
            else ReleaseType.EP
            if len(tracks) <= 6
            else ReleaseType.ALBUM
        )
    else:
        resolved_type = release_type

    artwork = media_package.thumbnails[0]
    fingerprint = build_submission_fingerprint(
        artist_id=artist_id,
        release_title=title,
        track_isrcs=[t.isrc for t in tracks],
        artwork_checksum=artwork.checksum,
        version=release_version,
    )

    return CanonicalReleasePackage(
        release_id=release_id,
        release_version=release_version,
        artist_id=artist_id,
        artist_name=artist_name,
        brand_id=media_package.brand_id,
        release_type=resolved_type,
        title=title,
        label_name=label_name,
        copyright_line=copyright_line,
        publisher_line=publisher_line,
        release_date=release_date,
        tracks=tracks,
        artwork_asset_uri=artwork.uri,
        artwork_checksum=artwork.checksum,
        territories=territories,
        source_manifest_ids=[
            *(m.p5_manifest_id for m in track_manifests),
            media_package.media_package_id,
        ],
        submission_fingerprint=fingerprint,
        created_at=now,
    )

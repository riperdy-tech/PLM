"""Release compiler for Phase P8 (P8 §11, Ruling R8, R9).

Compiles P5 clearance (ReleaseManifest) and P6 media package (MediaPackage)
into an immutable CanonicalReleasePackage with source manifest references
and deterministic submission fingerprint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..domain.enums import ISRCSource, ReleaseType
from ..domain.ids import (
    build_release_id,
    build_submission_fingerprint,
    build_track_id,
    compute_sha256,
    generate_deterministic_isrc,
)
from ..domain.models import CanonicalReleasePackage, DistributionTrack


class ReleaseClearanceError(Exception):
    """Raised when release candidate fails P5 clearance checks (Ruling R8)."""


def compile_canonical_release_package(
    p5_manifest: Any,
    media_package: Any,
    *,
    artist_id: str,
    artist_name: str,
    brand_id: str | None = None,
    release_title: str | None = None,
    release_version: int = 1,
    release_date: str | None = None,
    label_name: str = "Neon Records",
    copyright_line: str = "2026 Neon Records",
    publisher_line: str = "2026 Neon Music Publishing",
    territories: str = "WORLDWIDE",
    release_type: ReleaseType | None = None,
) -> CanonicalReleasePackage:
    """Compile P5 clearance manifest and P6 media package into CanonicalReleasePackage.

    Args:
        p5_manifest: ReleaseManifest instance or dict containing P5 clearance fields.
        media_package: MediaPackage instance or dict containing P6 media fields.
        artist_id: Canonical artist ID.
        artist_name: Artist display name.
        brand_id: Optional brand ID (defaults to media_package.brand_id).
        release_title: Optional release title override.
        release_version: Release version integer (defaults to 1).
        release_date: YYYY-MM-DD release date string.
        label_name: Distribution label name.
        copyright_line: Copyright string.
        publisher_line: Publishing string.
        territories: Distribution territory specification (defaults to WORLDWIDE).
        release_type: Optional explicit ReleaseType (single, ep, album).

    Returns:
        Immutable CanonicalReleasePackage ready for preflight and submission.
    """
    now = datetime.now(UTC)

    # 1. Verify P5 Clearance (Ruling R8, P5 §77, §92)
    p5_id = getattr(p5_manifest, "p5_manifest_id", None)
    if p5_id is None and isinstance(p5_manifest, dict):
        p5_id = p5_manifest.get("p5_manifest_id") or p5_manifest.get("release_manifest_id")
    if not p5_id:
        raise ReleaseClearanceError("P5 manifest missing p5_manifest_id")

    disposition = getattr(p5_manifest, "disposition", None)
    if disposition is None and isinstance(p5_manifest, dict):
        disposition = p5_manifest.get("disposition")
    
    # Handle enum or str
    disp_val = getattr(disposition, "value", disposition)
    if isinstance(disp_val, str):
        disp_val = disp_val.upper()

    cleared_dispositions = {"PASSED", "PASSED_WITH_WARNINGS", "CLEARED_BY_OVERRIDE"}
    if disp_val not in cleared_dispositions:
        raise ReleaseClearanceError(
            f"Release not cleared for distribution: disposition '{disp_val}' not in {cleared_dispositions}"
        )

    # 2. Extract P6 Media Package references
    p6_id = getattr(media_package, "media_package_id", None)
    if p6_id is None and isinstance(media_package, dict):
        p6_id = media_package.get("media_package_id")
    if not p6_id:
        raise ValueError("Media package missing media_package_id")

    pkg_brand_id = getattr(media_package, "brand_id", None)
    if pkg_brand_id is None and isinstance(media_package, dict):
        pkg_brand_id = media_package.get("brand_id")
    resolved_brand_id = brand_id or pkg_brand_id or "brand_default"

    # Extract artwork
    thumbnails = getattr(media_package, "thumbnails", None)
    if thumbnails is None and isinstance(media_package, dict):
        thumbnails = media_package.get("thumbnails", [])

    artwork_uri = ""
    artwork_checksum = ""
    if thumbnails and len(thumbnails) > 0:
        thumb = thumbnails[0]
        artwork_uri = thumb.get("uri", "") if isinstance(thumb, dict) else getattr(thumb, "uri", "")
        artwork_checksum = (
            thumb.get("checksum", "") if isinstance(thumb, dict) else getattr(thumb, "checksum", "")
        )
    if not artwork_uri:
        artwork_uri = f"media/artwork/{p6_id}.jpg"
    if not artwork_checksum:
        artwork_checksum = compute_sha256(artwork_uri)

    # 3. Extract Tracks and Metadata
    tracklist = getattr(media_package, "tracklist", None)
    if tracklist is None and isinstance(media_package, dict):
        tracklist = media_package.get("tracklist", [])

    metadata_candidates = getattr(media_package, "metadata_candidates", None)
    if metadata_candidates is None and isinstance(media_package, dict):
        metadata_candidates = media_package.get("metadata_candidates", [])

    # Derive title
    title = release_title
    if not title and metadata_candidates and len(metadata_candidates) > 0:
        c0 = metadata_candidates[0]
        title = c0.get("title") if isinstance(c0, dict) else getattr(c0, "title", None)
    if not title and tracklist and len(tracklist) > 0:
        t0 = tracklist[0]
        title = t0.get("title") if isinstance(t0, dict) else getattr(t0, "title", None)
    if not title:
        title = "Untitled Release"

    slug = title.lower().replace(" ", "_").strip()
    release_id = build_release_id(resolved_brand_id, slug)

    # Build tracks
    tracks: list[DistributionTrack] = []
    if tracklist and len(tracklist) > 0:
        for idx, t_data in enumerate(tracklist, start=1):
            if isinstance(t_data, dict):
                t_title = t_data.get("title", f"Track {idx}")
                t_version = t_data.get("version_title")
                t_duration = float(t_data.get("duration_seconds", 180.0))
                t_uri = t_data.get("audio_uri") or t_data.get("audio_asset_uri") or ""
                t_checksum = t_data.get("audio_checksum") or compute_sha256(t_uri or f"audio_{idx}")
                t_isrc = t_data.get("isrc")
                t_explicit = bool(t_data.get("explicit", False))
                t_disc = int(t_data.get("disc_number", 1))
            else:
                t_title = getattr(t_data, "title", f"Track {idx}")
                t_version = getattr(t_data, "version_title", None)
                t_duration = float(getattr(t_data, "duration_seconds", 180.0))
                t_uri = getattr(t_data, "audio_uri", "") or getattr(t_data, "audio_asset_uri", "")
                t_checksum = getattr(t_data, "audio_checksum", "") or compute_sha256(t_uri or f"audio_{idx}")
                t_isrc = getattr(t_data, "isrc", None)
                t_explicit = bool(getattr(t_data, "explicit", False))
                t_disc = int(getattr(t_data, "disc_number", 1))

            if not t_uri:
                t_uri = f"media/audio/{release_id}_{idx:02d}.wav"

            if t_isrc:
                isrc_source = ISRCSource.USER_SUPPLIED
            else:
                t_isrc = generate_deterministic_isrc(sequence_number=idx)
                isrc_source = ISRCSource.INTERNAL_ALLOCATION

            track_id = build_track_id(release_id, idx)
            tracks.append(
                DistributionTrack(
                    id=f"rec_{track_id}",
                    track_id=track_id,
                    release_id=release_id,
                    track_number=idx,
                    disc_number=t_disc,
                    title=t_title,
                    version_title=t_version,
                    isrc=t_isrc,
                    isrc_source=isrc_source,
                    duration_seconds=t_duration,
                    audio_asset_uri=t_uri,
                    audio_checksum=t_checksum,
                    explicit=t_explicit,
                    created_at=now,
                )
            )
    else:
        # Single track fallback from media package audio master
        audio_uri = getattr(media_package, "audio_master_uri", None)
        if audio_uri is None and isinstance(media_package, dict):
            audio_uri = media_package.get("audio_master_uri", "")
        audio_checksum = getattr(media_package, "audio_checksum", None)
        if audio_checksum is None and isinstance(media_package, dict):
            audio_checksum = media_package.get("audio_checksum", "")

        if not audio_uri:
            audio_uri = f"media/audio/{release_id}_01.wav"
        if not audio_checksum:
            audio_checksum = compute_sha256(audio_uri)

        track_id = build_track_id(release_id, 1)
        isrc = generate_deterministic_isrc(sequence_number=1)
        tracks.append(
            DistributionTrack(
                id=f"rec_{track_id}",
                track_id=track_id,
                release_id=release_id,
                track_number=1,
                disc_number=1,
                title=title,
                version_title=None,
                isrc=isrc,
                isrc_source=ISRCSource.INTERNAL_ALLOCATION,
                duration_seconds=180.0,
                audio_asset_uri=audio_uri,
                audio_checksum=audio_checksum,
                explicit=False,
                created_at=now,
            )
        )

    # 4. Resolve release type
    if release_type is None:
        if len(tracks) == 1:
            resolved_type = ReleaseType.SINGLE
        elif len(tracks) <= 6:
            resolved_type = ReleaseType.EP
        else:
            resolved_type = ReleaseType.ALBUM
    else:
        resolved_type = release_type

    # 5. Deterministic fingerprint (P8 §11, §68)
    track_isrcs = [t.isrc for t in tracks]
    fingerprint = build_submission_fingerprint(
        artist_id=artist_id,
        release_title=title,
        track_isrcs=track_isrcs,
        artwork_checksum=artwork_checksum,
        version=release_version,
    )

    resolved_release_date = release_date or now.strftime("%Y-%m-%d")

    return CanonicalReleasePackage(
        release_id=release_id,
        release_version=release_version,
        artist_id=artist_id,
        artist_name=artist_name,
        brand_id=resolved_brand_id,
        release_type=resolved_type,
        title=title,
        label_name=label_name,
        copyright_line=copyright_line,
        publisher_line=publisher_line,
        release_date=resolved_release_date,
        tracks=tracks,
        artwork_asset_uri=artwork_uri,
        artwork_checksum=artwork_checksum,
        territories=territories,
        source_manifest_ids=[str(p5_id), str(p6_id)],
        submission_fingerprint=fingerprint,
        created_at=now,
    )

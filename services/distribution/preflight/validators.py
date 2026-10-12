"""Distribution preflight validation (P8 §12, §13, §21).

Verifies identity, metadata, audio, artwork, track ordering, and rights clearance
before any external distributor submission.
"""

from __future__ import annotations

import re
from datetime import date

from ..domain.models import ArtistProfile, CanonicalReleasePackage
from ..identifiers.isrc import validate_isrc_format


class DistributionPreflightError(Exception):
    """Raised when release package fails preflight checks (P8 §12)."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Distribution preflight failed with {len(errors)} error(s): {'; '.join(errors)}")


DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_canonical_package(
    package: CanonicalReleasePackage,
    artist: ArtistProfile | None = None,
) -> list[str]:
    """Validate canonical release package against distribution rules.

    Returns a list of error strings. If empty, package passed preflight.
    """
    errors: list[str] = []

    # 1. Identity & Artist validation
    if not package.title or not package.title.strip():
        errors.append("Release title cannot be empty")
    elif len(package.title) > 255:
        errors.append("Release title exceeds 255 characters")
    elif any(ord(c) < 32 for c in package.title):
        errors.append("Release title contains illegal control characters")

    if artist is not None and package.artist_id != artist.id:
        errors.append(
            f"Release package artist_id '{package.artist_id}' does not match artist profile '{artist.id}'"
        )

    # 2. Release date format
    if not DATE_REGEX.match(package.release_date):
        errors.append(f"Release date '{package.release_date}' must follow YYYY-MM-DD format")
    else:
        try:
            date.fromisoformat(package.release_date)
        except ValueError:
            errors.append(f"Release date '{package.release_date}' is not a valid calendar date")

    # 3. Source manifest clearance references (Ruling R9, P8 §11, §39)
    if len(package.source_manifest_ids) < 2:
        errors.append(
            "Release package must reference at least 2 source manifests (P5 clearance and P6 media package)"
        )

    # 4. Artwork validation
    if not package.artwork_asset_uri or not package.artwork_asset_uri.strip():
        errors.append("Artwork asset URI is required")
    if not package.artwork_checksum or not package.artwork_checksum.strip():
        errors.append("Artwork checksum is required")

    # 5. Tracks validation
    if not package.tracks:
        errors.append("Release package must contain at least one track")
        return errors

    seen_track_numbers: set[int] = set()
    seen_isrcs: set[str] = set()

    for idx, track in enumerate(package.tracks, start=1):
        # Track numbering sequential from 1
        if track.track_number in seen_track_numbers:
            errors.append(f"Duplicate track number {track.track_number} detected")
        seen_track_numbers.add(track.track_number)

        if track.disc_number < 1:
            errors.append(f"Track {track.track_number} has invalid disc number {track.disc_number}")

        # Title
        if not track.title or not track.title.strip():
            errors.append(f"Track {track.track_number} title cannot be empty")
        elif any(ord(c) < 32 for c in track.title):
            errors.append(f"Track {track.track_number} title contains illegal control characters")

        # Duration
        if track.duration_seconds <= 0:
            errors.append(f"Track {track.track_number} duration must be positive")

        # Audio
        if not track.audio_asset_uri or not track.audio_asset_uri.strip():
            errors.append(f"Track {track.track_number} audio asset URI is required")
        if not track.audio_checksum or not track.audio_checksum.strip():
            errors.append(f"Track {track.track_number} audio checksum is required")

        # ISRC validation
        if not track.isrc or not validate_isrc_format(track.isrc):
            errors.append(f"Track {track.track_number} has invalid ISRC '{track.isrc}'")
        elif track.isrc in seen_isrcs:
            errors.append(f"Duplicate ISRC '{track.isrc}' detected on track {track.track_number}")
        seen_isrcs.add(track.isrc)

    # Check track numbers start at 1 and have no gaps
    expected_numbers = set(range(1, len(package.tracks) + 1))
    if seen_track_numbers != expected_numbers:
        errors.append(
            f"Track numbers must be sequential from 1 without gaps. Found {sorted(seen_track_numbers)}, expected {sorted(expected_numbers)}"
        )

    return errors

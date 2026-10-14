"""Artist identity manager and safety validation for Phase P8 (P8 §9, §10).

Treats artist identity as a first-class object, protects against store name collisions,
and validates artist metadata before release submission.
"""

from __future__ import annotations

import re
from datetime import datetime

from plm_platform.clock import require_aware
from sqlalchemy.orm import Session

from ..domain.enums import ArtistType
from ..domain.ids import compute_sha256
from ..domain.models import ArtistProfile
from ..storage.repositories import get_artist_by_name, insert_artist


class ArtistCollisionError(Exception):
    """Raised when an artist name conflicts or matches across unexpected brand boundaries."""


def normalize_artist_name(name: str) -> str:
    """Normalize an artist name for collision detection."""
    # Strip excess spaces, lowercase, collapse internal whitespace
    norm = re.sub(r"\s+", " ", name.strip().lower())
    return norm


def get_or_create_artist(
    session: Session,
    name: str,
    brand_id: str,
    *,
    artist_type: ArtistType = ArtistType.VIRTUAL,
    spotify_artist_id: str | None = None,
    apple_artist_id: str | None = None,
    now: datetime,
) -> ArtistProfile:
    """Retrieve existing artist by name or create a new artist profile.

    If an artist with the same normalized name exists under a DIFFERENT brand_id,
    raises ArtistCollisionError to protect against accidental catalog hijacking (P8 §10).
    """
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Artist name cannot be blank")

    normalized = normalize_artist_name(clean_name)
    existing = get_artist_by_name(session, normalized)

    if existing:
        if existing.brand_id != brand_id:
            raise ArtistCollisionError(
                f"Artist name collision: '{clean_name}' already exists for brand '{existing.brand_id}', "
                f"cannot reuse for brand '{brand_id}' (P8 §10 collision safety rule)"
            )
        return existing

    now = require_aware(now)
    artist_id = f"art_{compute_sha256(f'{brand_id}|{normalized}')[:16]}"
    
    artist = ArtistProfile(
        id=artist_id,
        canonical_name=clean_name,
        normalized_name=normalized,
        artist_type=artist_type,
        brand_id=brand_id,
        spotify_artist_id=spotify_artist_id,
        apple_artist_id=apple_artist_id,
        created_at=now,
        updated_at=now,
    )
    insert_artist(session, artist)
    return artist


def validate_artist_profile(artist: ArtistProfile) -> list[str]:
    """Validate artist profile completeness and safety rules (P8 §10)."""
    errors: list[str] = []

    if not artist.canonical_name or not artist.canonical_name.strip():
        errors.append("Artist canonical name must not be empty")

    if len(artist.canonical_name) > 255:
        errors.append("Artist canonical name exceeds 255 characters")

    if not artist.brand_id:
        errors.append("Artist brand_id must be specified")

    # Safety: check for illegal control characters
    if any(ord(c) < 32 for c in artist.canonical_name):
        errors.append("Artist name contains illegal control characters")

    return errors

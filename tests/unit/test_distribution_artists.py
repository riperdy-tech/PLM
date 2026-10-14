"""Unit tests for artist identity management and safety rules (P8 §9, §10)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from distribution.artists.manager import (
    ArtistCollisionError,
    get_or_create_artist,
    normalize_artist_name,
    validate_artist_profile,
)
from distribution.domain.enums import ArtistType

from tests.support.distribution_db import distribution_db_session

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_normalize_artist_name() -> None:
    assert normalize_artist_name("  Neon  Seoul  ") == "neon seoul"
    assert normalize_artist_name("THE  MIDNIGHT") == "the midnight"


def test_get_or_create_artist_and_idempotency() -> None:
    with distribution_db_session() as session:
        art1 = get_or_create_artist(session, "Neon Seoul", "brand_neon", now=NOW)
        assert art1.canonical_name == "Neon Seoul"
        assert art1.normalized_name == "neon seoul"
        assert art1.artist_type == ArtistType.VIRTUAL
        assert art1.created_at == NOW

        # Calling again with same name and brand returns existing profile
        art2 = get_or_create_artist(session, "neon seoul", "brand_neon", now=NOW)
        assert art1.id == art2.id


def test_artist_name_collision_across_brands_rejected() -> None:
    with distribution_db_session() as session:
        get_or_create_artist(session, "Nova Star", "brand_alpha", now=NOW)

        # Attempting to use the exact same artist name under a different brand raises ArtistCollisionError
        with pytest.raises(ArtistCollisionError, match="Artist name collision"):
            get_or_create_artist(session, "Nova Star", "brand_beta", now=NOW)


def test_validate_artist_profile() -> None:
    with distribution_db_session() as session:
        artist = get_or_create_artist(session, "Valid Artist", "brand_valid", now=NOW)
        errors = validate_artist_profile(artist)
        assert errors == []

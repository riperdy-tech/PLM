"""Artist identity management for Phase P8 (P8 §9, §10)."""

from .manager import ArtistCollisionError, get_or_create_artist, validate_artist_profile

__all__ = ["ArtistCollisionError", "get_or_create_artist", "validate_artist_profile"]

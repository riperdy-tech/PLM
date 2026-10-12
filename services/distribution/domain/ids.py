"""Deterministic identifier and fingerprint generation for Phase P8 (P8 §7, §8, §11)."""

from __future__ import annotations

import hashlib


def compute_sha256(content: bytes | str) -> str:
    """Compute hex SHA-256 digest of string or bytes."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def generate_deterministic_isrc(
    country: str = "US",
    registrant: str = "PLM",
    year_two_digits: str = "26",
    sequence_number: int = 1,
) -> str:
    """Generate a standard 12-character ISRC: CC-XXX-YY-NNNNN."""
    return f"{country.upper()}{registrant.upper()[:3]}{year_two_digits}{sequence_number:05d}"


def build_submission_fingerprint(
    artist_id: str,
    release_title: str,
    track_isrcs: list[str],
    artwork_checksum: str,
    version: int = 1,
) -> str:
    """Build deterministic submission fingerprint (P8 §11, §68)."""
    canonical = (
        f"{artist_id}|{release_title}|{','.join(sorted(track_isrcs))}|{artwork_checksum}|{version}"
    )
    return compute_sha256(canonical)


def build_release_id(brand_id: str, slug: str) -> str:
    """Build deterministic internal release identifier."""
    canonical = f"{brand_id}|{slug}"
    return f"rel_{compute_sha256(canonical)[:16]}"


def build_track_id(release_id: str, track_number: int) -> str:
    """Build deterministic track identifier."""
    return f"trk_{release_id.replace('rel_', '')}_{track_number:02d}"

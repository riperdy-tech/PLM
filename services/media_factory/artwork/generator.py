"""Artwork and thumbnail generation engine (P6 §8.5, §17, §18).

Generates cover artwork and multiple thumbnail variants for A/B testing.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from ..domain.models import ThumbnailVariant
from ..visuals.planner import BrandProfile

__all__ = ["ArtworkResult", "generate_artwork_and_thumbnails"]


class ArtworkResult:
    """Generated cover art and thumbnail variants."""

    def __init__(
        self,
        cover_art_uri: str,
        cover_art_checksum: str,
        thumbnails: list[ThumbnailVariant],
    ) -> None:
        self.cover_art_uri = cover_art_uri
        self.cover_art_checksum = cover_art_checksum
        self.thumbnails = thumbnails


def generate_artwork_and_thumbnails(
    *,
    media_package_id_str: str,
    brand_profile: BrandProfile,
    playlist_title: str,
    variant_keys: list[str] | None = None,
    now: datetime,
) -> ArtworkResult:
    """Generate cover art and thumbnail candidate variants."""
    keys = variant_keys or ["thumb_a_hero", "thumb_b_bold_text", "thumb_c_minimal"]

    # 1. Generate canonical cover art (1:1)
    cover_seed = f"cover:{media_package_id_str}:{brand_profile.brand_id}:{playlist_title}"
    cover_checksum = hashlib.sha256(cover_seed.encode("utf-8")).hexdigest()
    cover_uri = f"media/artwork/{brand_profile.brand_id}/{media_package_id_str}_cover.webp"

    # 2. Generate thumbnail candidate variants (16:9 1280x720)
    thumbnails: list[ThumbnailVariant] = []
    for k in keys:
        thumb_seed = f"thumb:{media_package_id_str}:{k}:{playlist_title}"
        t_hash = hashlib.sha256(thumb_seed.encode("utf-8")).hexdigest()
        t_uri = f"media/thumbnails/{brand_profile.brand_id}/{media_package_id_str}_{k}.webp"

        text_label = playlist_title if "bold_text" in k else ""
        variant = ThumbnailVariant.create(
            media_package_id_str=media_package_id_str,
            variant_key=k,
            source_assets=[cover_uri],
            uri=t_uri,
            checksum=t_hash,
            text_payload=text_label,
            width=1280,
            height=720,
            layout_version=brand_profile.version,
            now=now,
        )
        thumbnails.append(variant)

    return ArtworkResult(
        cover_art_uri=cover_uri,
        cover_art_checksum=cover_checksum,
        thumbnails=thumbnails,
    )

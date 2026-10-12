"""Deterministic UUID5 ID generators for media factory entities (Ruling R14)."""

from __future__ import annotations

import uuid

__all__ = [
    "deterministic_id",
    "media_package_id",
    "playlist_session_id",
    "playlist_track_id",
    "render_job_id",
    "thumbnail_variant_id",
    "visual_asset_id",
    "visual_session_id",
]

_PLM_MEDIA_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "media.factory.plm.internal")


def deterministic_id(prefix: str, *keys: str) -> str:
    """Generate a reproducible, collision-resistant identifier from domain keys."""
    combined = ":".join(keys)
    digest = uuid.uuid5(_PLM_MEDIA_NAMESPACE, combined).hex[:16]
    return f"{prefix}_{digest}"


def playlist_session_id(experiment_id: str, brand_id: str, version: str = "v1") -> str:
    return deterministic_id("ply", experiment_id, brand_id, version)


def playlist_track_id(session_id: str, song_id: str, position: int) -> str:
    return deterministic_id("ptrk", session_id, song_id, str(position))


def visual_session_id(playlist_session_id_str: str, visual_profile: str) -> str:
    return deterministic_id("vsess", playlist_session_id_str, visual_profile)


def visual_asset_id(visual_session_id_str: str, asset_type: str, checksum: str) -> str:
    return deterministic_id("vast", visual_session_id_str, asset_type, checksum[:16])


def thumbnail_variant_id(media_package_id_str: str, variant_key: str) -> str:
    return deterministic_id("thmb", media_package_id_str, variant_key)


def render_job_id(media_package_id_str: str, render_profile: str, attempt: int) -> str:
    return deterministic_id("rjob", media_package_id_str, render_profile, str(attempt))


def media_package_id(experiment_id: str, brand_id: str, package_version: str = "v1") -> str:
    return deterministic_id("mpkg", experiment_id, brand_id, package_version)

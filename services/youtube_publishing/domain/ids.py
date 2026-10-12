"""Deterministic identifier and hash generation for P7 YouTube Publishing Engine (P7 §11.1)."""

from __future__ import annotations

import hashlib


def compute_sha256(content: bytes | str) -> str:
    """Compute hex SHA-256 digest of string or bytes."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def build_idempotency_key(
    channel_id: str,
    media_package_id: str,
    video_checksum: str,
    version: str = "v1",
) -> str:
    """Build deterministic publish key (P7 §11.1)."""
    canonical = f"{channel_id}|{media_package_id}|{video_checksum}|{version}"
    return f"idem_{compute_sha256(canonical)[:32]}"


def build_manifest_id(
    channel_id: str,
    media_package_id: str,
    video_checksum: str,
) -> str:
    """Build deterministic publish manifest identifier."""
    canonical = f"manifest|{channel_id}|{media_package_id}|{video_checksum}"
    return f"pub_man_{compute_sha256(canonical)[:24]}"


def build_attempt_id(
    manifest_id: str,
    attempt_number: int = 1,
) -> str:
    """Build publish attempt identifier."""
    return f"att_{manifest_id.replace('pub_man_', '')}_{attempt_number:02d}"


def build_publication_record_id(
    youtube_video_id: str,
) -> str:
    """Build publication record identifier."""
    return f"pub_rec_{youtube_video_id}"

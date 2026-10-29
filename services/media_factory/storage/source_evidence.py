"""Read-only cross-check of stored P6 album evidence against its source records.

This verifies only database metadata, not source audio bytes, rights or consent.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from .models import playlist_sessions_table, playlist_tracks_table


def archived_sources_match(
    session: Session,
    package: RowMapping,
    tracklist: list[dict[str, Any]],
    validated: list[tuple[str, str, tuple[float, ...]]],
) -> bool:
    """Reject absent/mismatched original playlist rows without external reads.

    Separate SELECTs under READ COMMITTED are not an atomic snapshot. This
    check must never be represented as a verified original audio checksum.
    """
    playlist_id = package["playlist_session_id"]
    header = session.execute(
        sa.select(playlist_sessions_table)
        .where(playlist_sessions_table.c.id == playlist_id)
    ).mappings().one_or_none()
    if header is None or any(
        header[field] != package[field]
        for field in ("release_manifest_id", "brand_id", "experiment_id", "content_arm_id")
    ):
        return False

    original_tracks = session.execute(
        sa.select(playlist_tracks_table)
        .where(playlist_tracks_table.c.playlist_session_id == playlist_id)
        .order_by(playlist_tracks_table.c.position, playlist_tracks_table.c.id)
    ).mappings().all()
    if len(original_tracks) != len(validated):
        return False
    return all(
        original["position"] == index - 1
        and original["song_id"] == candidate_id == saved["song_id"]
        and original["source_artifact_id"] == saved.get("source_artifact_id")
        and original["audio_checksum"] == checksum
        and isinstance(original["audio_uri"], str) and bool(original["audio_uri"])
        for index, (original, saved, (candidate_id, checksum, _)) in enumerate(
            zip(original_tracks, tracklist, validated, strict=True), 1
        )
    )

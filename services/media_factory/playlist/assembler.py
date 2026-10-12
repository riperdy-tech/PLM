"""Playlist assembler and Seam C5 consumer (P6 §4, §9, §10, Rulings R7, R8).

Consumes P5 release manifests, enforces clearance gates, and sequences tracks
into an immutable playlist timeline.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..domain.enums import PlaylistStatus, TransitionType
from ..domain.models import PlaylistSession, PlaylistTrack

__all__ = ["PlaylistAssemblerResult", "assemble_playlist"]

ALLOWED_DISPOSITIONS = {"PASSED", "PASSED_WITH_WARNINGS", "CLEARED_BY_OVERRIDE"}


class PlaylistAssemblerResult:
    """Outcome of playlist assembly."""

    def __init__(
        self,
        session: PlaylistSession,
        tracks: list[PlaylistTrack],
        total_duration_seconds: float,
        checksum: str,
    ) -> None:
        self.session = session
        self.tracks = tracks
        self.total_duration_seconds = total_duration_seconds
        self.checksum = checksum


def assemble_playlist(
    *,
    release_manifest_id: str,
    experiment_id: str,
    content_arm_id: str,
    brand_id: str,
    candidate_manifests: list[dict[str, Any]],
    track_details: list[dict[str, Any]],
    required_duration_seconds: float,
    transition_type: TransitionType = TransitionType.SHORT_CROSSFADE,
    transition_duration_seconds: float = 2.0,
    sequence_version: str = "v1",
) -> PlaylistAssemblerResult:
    """Assemble a cleared, sequenced playlist session from P5 manifests and track specs."""
    # 1. Enforce Ruling R7, R8: Validate clearance of all tracks
    tracks_by_cand = {t["candidate_id"]: t for t in track_details}

    for manifest in candidate_manifests:
        cand_id = manifest.get("candidate_id", "unknown")
        disposition = str(manifest.get("disposition", "")).upper()
        if disposition not in ALLOWED_DISPOSITIONS:
            raise ValueError(
                f"Track {cand_id} rejected by P6: disposition '{disposition}' is not cleared "
                f"(requires one of {sorted(ALLOWED_DISPOSITIONS)}) — Ruling R7, R8."
            )

    # 2. Sequence tracks into chronological timeline
    playlist_tracks: list[PlaylistTrack] = []
    current_offset = 0.0

    session = PlaylistSession.create(
        release_manifest_id=release_manifest_id,
        experiment_id=experiment_id,
        content_arm_id=content_arm_id,
        brand_id=brand_id,
        requested_duration_seconds=required_duration_seconds,
        sequence_version=sequence_version,
    )

    for pos, manifest in enumerate(candidate_manifests):
        cand_id = manifest["candidate_id"]
        detail = tracks_by_cand.get(cand_id, {})
        duration = float(detail.get("duration_seconds", 120.0))

        start_time = max(0.0, current_offset)
        end_time = start_time + duration

        # Crossfade overlap for subsequent tracks
        next_offset = end_time - (
            transition_duration_seconds if pos < len(candidate_manifests) - 1 else 0.0
        )

        track = PlaylistTrack.create(
            session_id=session.id,
            song_id=cand_id,
            source_artifact_id=manifest.get(
                "canonical_artifact_id", detail.get("artifact_id", "art_unknown")
            ),
            position=pos,
            start_offset_seconds=start_time,
            end_offset_seconds=end_time,
            transition_type=transition_type,
            transition_duration_seconds=transition_duration_seconds,
            title=detail.get("title", f"Track {pos + 1}"),
            audio_uri=detail.get("audio_uri", ""),
            audio_checksum=detail.get("audio_checksum", ""),
        )
        playlist_tracks.append(track)
        current_offset = next_offset

    total_duration = current_offset if playlist_tracks else 0.0

    # 3. Calculate sequence checksum
    seq_repr = json.dumps(
        [
            {
                "pos": t.position,
                "song_id": t.song_id,
                "start": t.start_offset_seconds,
                "end": t.end_offset_seconds,
            }
            for t in playlist_tracks
        ],
        sort_keys=True,
    ).encode("utf-8")
    seq_hash = hashlib.sha256(seq_repr).hexdigest()

    session.actual_duration_seconds = total_duration
    session.sequence_checksum = seq_hash
    session.status = PlaylistStatus.ASSEMBLED
    session.tracks = playlist_tracks

    return PlaylistAssemblerResult(
        session=session,
        tracks=playlist_tracks,
        total_duration_seconds=total_duration,
        checksum=seq_hash,
    )

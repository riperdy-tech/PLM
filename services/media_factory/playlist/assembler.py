"""Playlist assembler and Seam C5 consumer (P6 §4, §9, §10, Rulings R7, R8, ADR-0028).

P6 assembles the playlist-level manifest itself (R7) from P5's per-track manifests,
parsed with the shared :class:`~plm_contracts.seams.c5.ReleaseManifestPayload`. The
rules, each of which refuses the assembly with a reason code instead of guessing:

* a track is admitted only if P5 passed it, or an audited override cleared it (R8);
* every track must share one experiment lineage, which becomes the package's lineage;
* a track's duration, artifact URI and checksum are P5's recorded facts, never defaults;
* an empty playlist is refused.

Track titles are P6's own output: ``Track <n>`` by position until the metadata stage
supplies real titles.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from plm_contracts import ErrorShape
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c5 import ReleaseManifestPayload

from ..domain.enums import PlaylistStatus, TransitionType
from ..domain.models import PlaylistSession, PlaylistTrack

__all__ = [
    "PlaylistAssemblerResult",
    "PlaylistAssemblyError",
    "assemble_playlist",
    "playlist_manifest_id",
]


class PlaylistAssemblyError(ValueError):
    """P6 refused to assemble; ``error`` carries the reason code and offending ids."""

    def __init__(self, code: str, message: str, field_paths: list[str] | None = None) -> None:
        self.error = ErrorShape(code=code, field_paths=field_paths or [], message=message)
        super().__init__(f"{code}: {message}")


class PlaylistAssemblerResult:
    """Outcome of playlist assembly."""

    def __init__(
        self,
        session: PlaylistSession,
        tracks: list[PlaylistTrack],
        total_duration_seconds: float,
        checksum: str,
        lineage: ExperimentLineage,
    ) -> None:
        self.session = session
        self.tracks = tracks
        self.total_duration_seconds = total_duration_seconds
        self.checksum = checksum
        self.lineage = lineage


def playlist_manifest_id(manifests: Sequence[ReleaseManifestPayload]) -> str:
    """P6's playlist-level manifest id: content-addressed over the ordered P5 manifests."""
    joined = "|".join(m.p5_manifest_id for m in manifests)
    return f"plm_{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:16]}"


def common_lineage(manifests: Sequence[ReleaseManifestPayload]) -> ExperimentLineage:
    """The one lineage every track shares, or a refusal naming the mismatch."""
    if not manifests:
        raise PlaylistAssemblyError("empty_playlist", "no track manifests to assemble")
    first = manifests[0].lineage
    mixed = [m.candidate_id for m in manifests if m.lineage != first]
    if mixed:
        raise PlaylistAssemblyError(
            "mixed_lineage",
            f"tracks {mixed} belong to a different experiment lineage than "
            f"{manifests[0].candidate_id!r}; a package is attributable to one arm",
            ["track_manifests"],
        )
    return first


def assemble_playlist(
    *,
    manifests: Sequence[ReleaseManifestPayload],
    brand_id: str,
    required_duration_seconds: float,
    transition_type: TransitionType = TransitionType.SHORT_CROSSFADE,
    transition_duration_seconds: float = 2.0,
    sequence_version: str = "v1",
) -> PlaylistAssemblerResult:
    """Assemble a cleared, sequenced playlist session from P5 manifests."""
    # 1. R7/R8: every track cleared, one lineage.
    blocked = [m.candidate_id for m in manifests if not m.cleared_for_release]
    if blocked:
        raise PlaylistAssemblyError(
            "track_not_cleared",
            f"tracks {blocked} were not passed by P5 and carry no audited override (R7, R8)",
            ["track_manifests"],
        )
    lineage = common_lineage(manifests)
    rel_id = playlist_manifest_id(manifests)

    # 2. Sequence tracks into a timeline (input order; sequencing optimisation is R4).
    session = PlaylistSession.create(
        release_manifest_id=rel_id,
        experiment_id=lineage.experiment_id,
        content_arm_id=lineage.content_arm_id,
        brand_id=brand_id,
        requested_duration_seconds=required_duration_seconds,
        sequence_version=sequence_version,
    )

    playlist_tracks: list[PlaylistTrack] = []
    current_offset = 0.0
    for pos, manifest in enumerate(manifests):
        start_time = max(0.0, current_offset)
        end_time = start_time + manifest.measured_duration_seconds
        is_last = pos == len(manifests) - 1
        next_offset = end_time - (0.0 if is_last else transition_duration_seconds)

        playlist_tracks.append(
            PlaylistTrack.create(
                session_id=session.id,
                song_id=manifest.candidate_id,
                source_artifact_id=manifest.canonical_artifact_id,
                position=pos,
                start_offset_seconds=start_time,
                end_offset_seconds=end_time,
                transition_type=transition_type,
                transition_duration_seconds=transition_duration_seconds,
                title=f"Track {pos + 1}",
                audio_uri=manifest.artifact_uri,
                audio_checksum=manifest.artifact_sha256,
            )
        )
        current_offset = next_offset

    total_duration = current_offset

    # 3. Sequence checksum
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
        lineage=lineage,
    )

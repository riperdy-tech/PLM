"""Playlist assembler and Seam C5 consumer (P6 §4, §9, §10, Rulings R7, R8, ADR-0028).

P6 assembles the playlist-level manifest itself (R7) from P5's per-track manifests,
parsed with the shared :class:`~plm_contracts.seams.c5.ReleaseManifestPayload`. The
rules, each of which refuses the assembly with a reason code instead of guessing:

* a track is admitted only if P5 passed it, or an audited override cleared it (R8);
* every track must share one experiment lineage, which becomes the package's lineage;
* a track's duration, artifact URI and checksum are P5's recorded facts, never defaults;
* an empty playlist is refused.

The order and the selection are the beam search of :mod:`.sequencer` over the features
P6 measured from the verified audio; the timeline follows the declared transition
(a crossfade overlaps consecutive tracks, a silence gap separates them, a hard cut
does neither). Track titles are P6's own output: no title reaches P6 (C5 carries
none), so each track gets a distinct, deterministic name from its id
(:mod:`.track_names`), which the chapters and tracklist use.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_contracts.seams.c5 import ReleaseManifestPayload

from ..audio.features import TrackFeatures
from ..domain.enums import PlaylistStatus, TransitionType
from ..domain.models import PlaylistSession, PlaylistTrack
from ..errors import MediaAssemblyError
from .sequencer import (
    DEFAULT_SEQUENCING_POLICY,
    SequenceItem,
    SequenceResult,
    SequencingPolicy,
    sequence_tracks,
)
from .track_names import track_names

__all__ = [
    "PlaylistAssemblerResult",
    "PlaylistAssemblyError",
    "assemble_playlist",
    "check_clearance",
    "common_lineage",
    "playlist_manifest_id",
    "transition_offset_seconds",
]


class PlaylistAssemblyError(MediaAssemblyError):
    """P6 refused to assemble the playlist; ``error`` carries the reason code."""


class PlaylistAssemblerResult:
    """Outcome of playlist assembly."""

    def __init__(
        self,
        session: PlaylistSession,
        tracks: list[PlaylistTrack],
        total_duration_seconds: float,
        checksum: str,
        lineage: ExperimentLineage,
        sequence: SequenceResult,
        manifests: list[ReleaseManifestPayload],
    ) -> None:
        self.session = session
        self.tracks = tracks
        self.total_duration_seconds = total_duration_seconds
        self.checksum = checksum
        self.lineage = lineage
        self.sequence = sequence
        self.manifests = manifests
        """The selected manifests, in playlist order."""


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


def check_clearance(manifests: Sequence[ReleaseManifestPayload]) -> ExperimentLineage:
    """R7/R8 before anything is fetched: every track cleared, one lineage."""
    blocked = [m.candidate_id for m in manifests if not m.cleared_for_release]
    if blocked:
        raise PlaylistAssemblyError(
            "track_not_cleared",
            f"tracks {blocked} were not passed by P5 and carry no audited override (R7, R8)",
            ["track_manifests"],
        )
    return common_lineage(manifests)


def transition_offset_seconds(transition_type: TransitionType, duration_seconds: float) -> float:
    """Signed gap between consecutive tracks on the timeline for a declared transition."""
    if transition_type is TransitionType.HARD_CUT:
        return 0.0
    if duration_seconds <= 0.0:
        raise PlaylistAssemblyError(
            "transition_duration_invalid",
            f"{transition_type.value} needs a positive duration, got {duration_seconds}",
            ["transition_duration_seconds"],
        )
    if transition_type is TransitionType.SILENCE_GAP:
        return duration_seconds
    return -duration_seconds


def assemble_playlist(
    *,
    manifests: Sequence[ReleaseManifestPayload],
    features: Mapping[str, TrackFeatures],
    brand_id: str,
    duration_range: DurationRangeSeconds,
    transition_type: TransitionType = TransitionType.SHORT_CROSSFADE,
    transition_duration_seconds: float = 2.0,
    policy: SequencingPolicy = DEFAULT_SEQUENCING_POLICY,
    now: datetime,
) -> PlaylistAssemblerResult:
    """Assemble a cleared, sequenced playlist session from P5 manifests.

    ``features`` holds the measured features of every manifest's track (by candidate
    id); a track without a features record is refused rather than scored as anything.
    ``duration_range`` is P3's declared playlist range; the session records its midpoint
    as the requested duration (R5).
    """
    lineage = check_clearance(manifests)
    missing = [m.candidate_id for m in manifests if m.candidate_id not in features]
    if missing:
        raise PlaylistAssemblyError(
            "features_not_measured", f"no measured features for tracks {missing}")
    offset = transition_offset_seconds(transition_type, transition_duration_seconds)
    by_id = {m.candidate_id: m for m in manifests}
    sequence = sequence_tracks(
        [SequenceItem(candidate_id=m.candidate_id, track_spec_id=m.track_spec_id,
                      duration_seconds=m.measured_duration_seconds,
                      features=features[m.candidate_id])
         for m in manifests],
        duration_range=duration_range,
        transition_offset_seconds=offset,
        policy=policy,
    )
    ordered = [by_id[candidate_id] for candidate_id in sequence.order]
    if offset < 0 and len(ordered) > 1:
        # A crossfade consumes the overlap from both neighbours of a track.
        short = [m.candidate_id for m in ordered
                 if m.measured_duration_seconds <= 2 * transition_duration_seconds]
        if short:
            raise PlaylistAssemblyError(
                "transition_longer_than_track",
                f"a {transition_duration_seconds}s crossfade needs tracks longer than twice "
                f"its length; {short} are not",
                ["transition_duration_seconds"],
            )
    rel_id = playlist_manifest_id(ordered)

    session = PlaylistSession.create(
        release_manifest_id=rel_id,
        experiment_id=lineage.experiment_id,
        content_arm_id=lineage.content_arm_id,
        brand_id=brand_id,
        requested_duration_seconds=sequence.target_seconds,
        sequence_version=policy.version,
        now=now,
    )
    recorded_duration = 0.0 if transition_type is TransitionType.HARD_CUT \
        else transition_duration_seconds
    playlist_tracks: list[PlaylistTrack] = []
    cursor = 0.0
    names = track_names([m.candidate_id for m in ordered])
    for pos, manifest in enumerate(ordered):
        start = cursor + (offset if pos else 0.0)
        end = start + manifest.measured_duration_seconds
        playlist_tracks.append(
            PlaylistTrack.create(
                session_id=session.id,
                song_id=manifest.candidate_id,
                source_artifact_id=manifest.canonical_artifact_id,
                position=pos,
                start_offset_seconds=start,
                end_offset_seconds=end,
                transition_type=transition_type,
                transition_duration_seconds=recorded_duration,
                sequence_reason=f"{policy.version}:beam_search",
                title=names[pos],
                audio_uri=manifest.artifact_uri,
                audio_checksum=manifest.artifact_sha256,
                now=now,
            )
        )
        cursor = end
    total_duration = cursor

    seq_repr = json.dumps(
        {
            "policy": policy.version,
            "transition": [transition_type.value, recorded_duration],
            "tracks": [
                {"pos": t.position, "song_id": t.song_id, "start": t.start_offset_seconds,
                 "end": t.end_offset_seconds, "checksum": t.audio_checksum}
                for t in playlist_tracks
            ],
        },
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
        sequence=sequence,
        manifests=ordered,
    )

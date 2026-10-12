"""Unit tests for Playlist Assembler and Seam C5 clearance enforcement (Ruling R7, R8)."""

from __future__ import annotations

import pytest
from media_factory.domain.enums import PlaylistStatus, TransitionType
from media_factory.playlist.assembler import assemble_playlist


def test_assemble_playlist_success() -> None:
    candidate_manifests = [
        {"candidate_id": "cand_01", "disposition": "PASSED", "canonical_artifact_id": "art_01"},
        {
            "candidate_id": "cand_02",
            "disposition": "PASSED_WITH_WARNINGS",
            "canonical_artifact_id": "art_02",
        },
        {
            "candidate_id": "cand_03",
            "disposition": "CLEARED_BY_OVERRIDE",
            "canonical_artifact_id": "art_03",
        },
    ]
    track_details = [
        {
            "candidate_id": "cand_01",
            "duration_seconds": 120.0,
            "title": "Track 1",
            "audio_uri": "s3://a1.wav",
            "audio_checksum": "c1",
        },
        {
            "candidate_id": "cand_02",
            "duration_seconds": 150.0,
            "title": "Track 2",
            "audio_uri": "s3://a2.wav",
            "audio_checksum": "c2",
        },
        {
            "candidate_id": "cand_03",
            "duration_seconds": 180.0,
            "title": "Track 3",
            "audio_uri": "s3://a3.wav",
            "audio_checksum": "c3",
        },
    ]

    res = assemble_playlist(
        release_manifest_id="rm_test_01",
        experiment_id="exp_01",
        content_arm_id="arm_01",
        brand_id="brand_lofi",
        candidate_manifests=candidate_manifests,
        track_details=track_details,
        required_duration_seconds=450.0,
        transition_type=TransitionType.SHORT_CROSSFADE,
        transition_duration_seconds=2.0,
    )

    assert res.session.status == PlaylistStatus.ASSEMBLED
    assert len(res.tracks) == 3
    assert res.session.sequence_checksum != ""

    # Check track offsets & crossfading
    # Track 0: 0.0 -> 120.0 (overlap by 2.0s means Track 1 starts at 118.0)
    assert res.tracks[0].position == 0
    assert res.tracks[0].start_offset_seconds == 0.0
    assert res.tracks[0].end_offset_seconds == 120.0

    assert res.tracks[1].position == 1
    assert res.tracks[1].start_offset_seconds == 118.0
    assert res.tracks[1].end_offset_seconds == 118.0 + 150.0


def test_assemble_playlist_rejects_uncleared_tracks() -> None:
    # Ruling R7, R8: Quarantine or Reject must fail closed
    candidate_manifests = [
        {"candidate_id": "cand_01", "disposition": "PASSED"},
        {"candidate_id": "cand_quarantined", "disposition": "QUARANTINED"},
    ]
    track_details = [
        {"candidate_id": "cand_01", "duration_seconds": 120.0},
        {"candidate_id": "cand_quarantined", "duration_seconds": 120.0},
    ]

    with pytest.raises(
        ValueError, match="rejected by P6: disposition 'QUARANTINED' is not cleared"
    ):
        assemble_playlist(
            release_manifest_id="rm_test_01",
            experiment_id="exp_01",
            content_arm_id="arm_01",
            brand_id="brand_lofi",
            candidate_manifests=candidate_manifests,
            track_details=track_details,
            required_duration_seconds=240.0,
        )

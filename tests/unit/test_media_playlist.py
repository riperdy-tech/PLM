"""Unit tests for the playlist assembler, the Seam C5 consumer (Rulings R7, R8, ADR-0028)."""

from __future__ import annotations

import pytest
from media_factory.domain.enums import PlaylistStatus, TransitionType
from media_factory.playlist.assembler import PlaylistAssemblyError, assemble_playlist

from tests.support.seams import lineage_fields, release_manifest

OVERRIDE = {
    "status": "CLEARED_BY_OVERRIDE",
    "quarantine_case_id": "qc_01",
    "auditor_id": "auditor_01",
    "resolved_at": "2026-09-25T12:00:00+00:00",
    "reason_codes": ["UNKNOWN_COMMERCIAL_LICENSE"],
}


def test_assemble_playlist_success() -> None:
    manifests = [
        release_manifest(candidate_id="cand_01", measured_duration_seconds=120.0),
        release_manifest(
            candidate_id="cand_02",
            disposition="PASSED_WITH_WARNINGS",
            measured_duration_seconds=150.0,
        ),
        release_manifest(
            candidate_id="cand_03",
            disposition="QUARANTINED",
            override=OVERRIDE,
            measured_duration_seconds=180.0,
        ),
    ]

    res = assemble_playlist(
        manifests=manifests,
        brand_id="brand_lofi",
        required_duration_seconds=450.0,
        transition_type=TransitionType.SHORT_CROSSFADE,
        transition_duration_seconds=2.0,
    )

    assert res.session.status == PlaylistStatus.ASSEMBLED
    assert len(res.tracks) == 3
    assert res.session.sequence_checksum != ""
    assert res.lineage == manifests[0].lineage
    # Track facts are P5's recorded facts, never defaults.
    assert res.tracks[0].audio_uri == manifests[0].artifact_uri
    assert res.tracks[0].audio_checksum == manifests[0].artifact_sha256
    # Track 0: 0.0 -> 120.0; a 2.0 s crossfade starts track 1 at 118.0
    assert res.tracks[0].start_offset_seconds == 0.0
    assert res.tracks[0].end_offset_seconds == 120.0
    assert res.tracks[1].start_offset_seconds == 118.0
    assert res.tracks[1].end_offset_seconds == 118.0 + 150.0


@pytest.mark.parametrize("disposition", ["QUARANTINED", "REJECTED"])
def test_uncleared_track_without_override_is_refused(disposition: str) -> None:
    manifests = [
        release_manifest(candidate_id="cand_01"),
        release_manifest(candidate_id="cand_blocked", disposition=disposition),
    ]
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=manifests, brand_id="brand_lofi", required_duration_seconds=240.0)
    assert exc.value.error.code == "track_not_cleared"
    assert "cand_blocked" in exc.value.error.message


def test_tracks_from_different_arms_are_refused() -> None:
    manifests = [
        release_manifest(candidate_id="cand_01"),
        release_manifest(candidate_id="cand_02", lineage=lineage_fields(content_arm_id="arm_other")),
    ]
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=manifests, brand_id="brand_lofi", required_duration_seconds=240.0)
    assert exc.value.error.code == "mixed_lineage"


def test_empty_playlist_is_refused() -> None:
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=[], brand_id="brand_lofi", required_duration_seconds=60.0)
    assert exc.value.error.code == "empty_playlist"

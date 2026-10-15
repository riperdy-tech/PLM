"""Unit tests for the playlist assembler, the Seam C5 consumer (Rulings R7, R8, ADR-0028).

Proves the refusals (uncleared track, mixed lineage, empty playlist, a track without
measured features), that track facts are P5's recorded facts, and that the timeline
follows the declared transition: a crossfade overlaps tracks, a silence gap separates
them, a hard cut does neither.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from media_factory.audio.features import TrackFeatures
from media_factory.domain.enums import PlaylistStatus, TransitionType
from media_factory.playlist.assembler import PlaylistAssemblyError, assemble_playlist
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_contracts.seams.c5 import ReleaseManifestPayload

from tests.support.seams import lineage_fields, release_manifest

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
OVERRIDE = {
    "status": "CLEARED_BY_OVERRIDE",
    "quarantine_case_id": "qc_01",
    "auditor_id": "auditor_01",
    "resolved_at": "2026-09-25T12:00:00+00:00",
    "reason_codes": ["UNKNOWN_COMMERCIAL_LICENSE"],
}


def _features(manifests: list[ReleaseManifestPayload]) -> dict[str, TrackFeatures]:
    return {
        m.candidate_id: TrackFeatures(
            candidate_id=m.candidate_id, bpm=120.0, bpm_confidence=0.9, camelot="8B",
            key_confidence=0.9, energy_in=0.5, energy_out=0.5, energy_mean=0.5,
            vocal_density=None, extractor_version="test",
            unknown_reasons={"vocal_density": "no_vocal_detector"})
        for m in manifests
    }


def _manifest(candidate_id: str, **overrides: object) -> ReleaseManifestPayload:
    return release_manifest(candidate_id=candidate_id, track_spec_id=f"trk_{candidate_id}",
                            **overrides)


def test_assemble_playlist_success() -> None:
    manifests = [
        _manifest("cand_01", measured_duration_seconds=120.0),
        _manifest("cand_02", disposition="PASSED_WITH_WARNINGS", measured_duration_seconds=150.0),
        _manifest("cand_03", disposition="QUARANTINED", override=OVERRIDE,
                  measured_duration_seconds=180.0),
    ]

    res = assemble_playlist(
        manifests=manifests, features=_features(manifests), brand_id="brand_lofi",
        duration_range=DurationRangeSeconds(min_seconds=430, max_seconds=460), transition_type=TransitionType.SHORT_CROSSFADE,
        transition_duration_seconds=2.0, now=NOW,
    )

    assert res.session.status == PlaylistStatus.ASSEMBLED
    assert res.session.sequence_version == "seq-beam-v2"
    # The session records the planning midpoint of the declared range.
    assert res.session.requested_duration_seconds == 445.0
    assert len(res.tracks) == 3
    assert res.session.sequence_checksum != ""
    assert res.lineage == manifests[0].lineage
    by_song = {m.candidate_id: m for m in manifests}
    for track in res.tracks:
        # Track facts are P5's recorded facts, never defaults.
        assert track.audio_uri == by_song[track.song_id].artifact_uri
        assert track.audio_checksum == by_song[track.song_id].artifact_sha256
    # A 2 s crossfade overlaps consecutive tracks.
    first, second = res.tracks[0], res.tracks[1]
    assert first.start_offset_seconds == 0.0
    assert second.start_offset_seconds == first.end_offset_seconds - 2.0
    assert res.total_duration_seconds == pytest.approx(450.0 - 4.0)


@pytest.mark.parametrize(("transition", "seconds", "gap"), [
    (TransitionType.HARD_CUT, 0.0, 0.0),
    (TransitionType.SILENCE_GAP, 3.0, 3.0),
    (TransitionType.LONG_CROSSFADE, 6.0, -6.0),
])
def test_the_timeline_follows_the_declared_transition(
        transition: TransitionType, seconds: float, gap: float) -> None:
    manifests = [_manifest(f"cand_{n}", measured_duration_seconds=60.0) for n in range(3)]
    res = assemble_playlist(
        manifests=manifests, features=_features(manifests), brand_id="b",
        duration_range=DurationRangeSeconds(min_seconds=int(175 + 2 * gap), max_seconds=int(185 + 2 * gap)),
        transition_type=transition,
        transition_duration_seconds=seconds, now=NOW,
    )
    starts = [t.start_offset_seconds for t in res.tracks]
    assert starts == [0.0, 60.0 + gap, 120.0 + 2 * gap]


@pytest.mark.parametrize("disposition", ["QUARANTINED", "REJECTED"])
def test_uncleared_track_without_override_is_refused(disposition: str) -> None:
    manifests = [_manifest("cand_01"), _manifest("cand_blocked", disposition=disposition)]
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=manifests, features=_features(manifests),
                          brand_id="brand_lofi",
                          duration_range=DurationRangeSeconds(min_seconds=230, max_seconds=250), now=NOW)
    assert exc.value.error.code == "track_not_cleared"
    assert "cand_blocked" in exc.value.error.message


def test_tracks_from_different_arms_are_refused() -> None:
    manifests = [
        _manifest("cand_01"),
        _manifest("cand_02", lineage=lineage_fields(content_arm_id="arm_other")),
    ]
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=manifests, features=_features(manifests),
                          brand_id="brand_lofi",
                          duration_range=DurationRangeSeconds(min_seconds=230, max_seconds=250), now=NOW)
    assert exc.value.error.code == "mixed_lineage"


def test_empty_playlist_is_refused() -> None:
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=[], features={}, brand_id="brand_lofi",
                          duration_range=DurationRangeSeconds(min_seconds=55, max_seconds=65), now=NOW)
    assert exc.value.error.code == "empty_playlist"


def test_a_track_without_measured_features_is_refused() -> None:
    manifests = [_manifest("cand_01"), _manifest("cand_02")]
    features = _features(manifests[:1])
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=manifests, features=features, brand_id="b",
                          duration_range=DurationRangeSeconds(min_seconds=290, max_seconds=310), now=NOW)
    assert exc.value.error.code == "features_not_measured"


def test_a_crossfade_longer_than_half_a_selected_track_is_refused() -> None:
    manifests = [_manifest(f"cand_{n}", measured_duration_seconds=3.0) for n in range(3)]
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=manifests, features=_features(manifests), brand_id="b",
                          duration_range=DurationRangeSeconds(min_seconds=5, max_seconds=5),
                          transition_duration_seconds=2.0,
                          now=NOW)
    assert exc.value.error.code == "transition_longer_than_track"
    # A single track has no transition to fit.
    alone = assemble_playlist(manifests=manifests[:1], features=_features(manifests[:1]),
                              brand_id="b", duration_range=DurationRangeSeconds(min_seconds=3, max_seconds=3),
                              transition_duration_seconds=2.0, now=NOW)
    assert len(alone.tracks) == 1

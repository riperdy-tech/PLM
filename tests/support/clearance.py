"""A clearance verifier over an in-memory P5 and an empty exception queue.

It runs the real ``verify_release_clearance`` rules; only the authorities are local. A
track is cleared only if the test hands its manifest to P5 here, so a test cannot pass
by bypassing the check.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from plm_contracts.seams import parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clearance import (
    ClearanceReport,
    ClearanceVerifier,
    TrackRef,
    verify_release_clearance,
)

__all__ = [
    "TEST_TRACK_ARTIFACT", "TEST_TRACK_CANDIDATE", "p5_clearing_test_track", "p5_holding",
    "test_track_refs",
]


class _P5:
    def __init__(self, manifests: Iterable[ReleaseManifestPayload]) -> None:
        self._by_id = {m.candidate_id: m for m in manifests}

    def release_manifest(self, candidate_id: str) -> ReleaseManifestPayload | None:
        return self._by_id.get(candidate_id)


class _NoHolds:
    def open_hold_codes(self, subject_ref: str) -> tuple[str, ...]:
        return ()


def p5_holding(*manifests: ReleaseManifestPayload | Mapping[str, Any]) -> ClearanceVerifier:
    parsed = [m if isinstance(m, ReleaseManifestPayload)
              else parse_seam(ReleaseManifestPayload, m) for m in manifests]
    p5 = _P5(parsed)

    def verify(tracks: Sequence[TrackRef]) -> ClearanceReport:
        return verify_release_clearance(tracks, manifests=p5, holds=_NoHolds())

    return verify


TEST_TRACK_CANDIDATE = "cand_test_track"
TEST_TRACK_ARTIFACT = "art_test_track"


def test_track_refs() -> tuple[Any, ...]:
    """The one recording that hand-built P7 manifests carry in unit tests."""
    # The service's own import path: the class must be the one the service validates.
    from youtube_publishing.domain.models import ManifestTrack

    return (ManifestTrack(candidate_id=TEST_TRACK_CANDIDATE, artifact_id=TEST_TRACK_ARTIFACT),)


def p5_clearing_test_track() -> ClearanceVerifier:
    """P5 holds a cleared manifest for exactly the test track."""
    from tests.support.seams import release_manifest

    return p5_holding(release_manifest(candidate_id=TEST_TRACK_CANDIDATE,
                                       canonical_artifact_id=TEST_TRACK_ARTIFACT))

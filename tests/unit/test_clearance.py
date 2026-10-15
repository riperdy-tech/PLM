"""Release clearance is re-verified against P5 and the exception queue before a write."""

from __future__ import annotations

import httpx
import pytest
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clearance import (
    ClearanceCode,
    ClearanceUnavailableError,
    HttpManifestSource,
    TrackRef,
    recording_subject,
    verify_release_clearance,
)

from tests.support.seams import release_manifest


class Manifests:
    def __init__(self, by_id: dict[str, ReleaseManifestPayload], *, down: bool = False) -> None:
        self.by_id, self.down = by_id, down

    def release_manifest(self, candidate_id: str) -> ReleaseManifestPayload | None:
        if self.down:
            raise ClearanceUnavailableError("P5 unreachable")
        return self.by_id.get(candidate_id)


class Holds:
    def __init__(self, open_codes: dict[str, tuple[str, ...]] | None = None,
                 *, down: bool = False) -> None:
        self.open_codes, self.down = open_codes or {}, down

    def open_hold_codes(self, subject_ref: str) -> tuple[str, ...]:
        if self.down:
            raise ClearanceUnavailableError("queue unreachable")
        return self.open_codes.get(subject_ref, ())


def _cleared(candidate: str, artifact: str) -> ReleaseManifestPayload:
    return release_manifest(candidate_id=candidate, canonical_artifact_id=artifact)


def _codes(report: object) -> list[ClearanceCode]:
    return [r.code for r in report.refusals]  # type: ignore[attr-defined]


def test_a_cleared_track_for_the_same_artifact_passes() -> None:
    report = verify_release_clearance(
        [TrackRef("cand_1", "art_1")],
        manifests=Manifests({"cand_1": _cleared("cand_1", "art_1")}), holds=Holds(),
    )
    assert report.cleared


def test_a_quarantined_track_is_refused_whatever_the_payload_claims() -> None:
    quarantined = release_manifest(candidate_id="cand_1", canonical_artifact_id="art_1",
                                   disposition="QUARANTINED")
    report = verify_release_clearance(
        [TrackRef("cand_1", "art_1")], manifests=Manifests({"cand_1": quarantined}),
        holds=Holds(),
    )
    assert _codes(report) == [ClearanceCode.NOT_CLEARED]


def test_no_manifest_a_different_artifact_and_an_open_hold_each_refuse() -> None:
    report = verify_release_clearance(
        [TrackRef("missing", "a"), TrackRef("swapped", "art_new"), TrackRef("disputed", "art_d")],
        manifests=Manifests({"swapped": _cleared("swapped", "art_old"),
                             "disputed": _cleared("disputed", "art_d")}),
        holds=Holds({recording_subject("disputed"): ("COPYRIGHT_DISPUTE",)}),
    )
    assert _codes(report) == [ClearanceCode.MANIFEST_MISSING, ClearanceCode.ARTIFACT_MISMATCH,
                              ClearanceCode.OPEN_HOLD]


@pytest.mark.parametrize("down", ["manifests", "holds"])
def test_an_unreachable_authority_refuses_and_never_passes(down: str) -> None:
    report = verify_release_clearance(
        [TrackRef("cand_1", "art_1")],
        manifests=Manifests({"cand_1": _cleared("cand_1", "art_1")}, down=down == "manifests"),
        holds=Holds(down=down == "holds"),
    )
    assert _codes(report) == [ClearanceCode.UNVERIFIABLE]


def test_an_empty_release_is_not_a_clearance() -> None:
    report = verify_release_clearance([], manifests=Manifests({}), holds=Holds())
    assert _codes(report) == [ClearanceCode.NO_TRACKS]


def test_http_source_maps_404_to_none_and_errors_to_unavailable() -> None:
    manifest = _cleared("cand_1", "art_1")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-PLM-Service-Token"] == "t"
        if request.url.path.endswith("/cand_1"):
            return httpx.Response(200, json=manifest.to_wire())
        if request.url.path.endswith("/gone"):
            return httpx.Response(404)
        return httpx.Response(503)

    source = HttpManifestSource("http://qa", token="t",
                                client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.release_manifest("cand_1") == manifest
    assert source.release_manifest("gone") is None
    with pytest.raises(ClearanceUnavailableError):
        source.release_manifest("broken")

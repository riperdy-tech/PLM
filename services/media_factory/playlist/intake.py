"""P6 intake: the C5 manifests of the requested recordings, fetched from their authority.

P6 assembles from candidate ids, not from manifest bodies a caller hands it (R4,
ADR-0028 consequence). For every id it asks P5 for the release manifest
(``GET /qa/release-manifests/{candidate_id}``) and the exception queue for open holds
on the recording, through :mod:`plm_platform.clearance`. It refuses, naming every
offending id:

* ``duplicate_track``: an id requested twice;
* ``manifest_missing``: P5 holds no manifest;
* ``manifest_mismatch``: P5 answered with another candidate's manifest;
* ``track_not_cleared``: neither passed nor cleared by an audited override (R8);
* ``open_hold``: an open exception-queue case on the recording (a dispute raised after
  clearance stops assembly too).

An authority that cannot be asked is ``clearance_unverifiable``, an infrastructure
error: nothing was decided, and nothing is assembled.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clearance import (
    ClearanceUnavailableError,
    HoldSource,
    ManifestSource,
    recording_subject,
)

from ..errors import MediaAssemblyError, MediaInfrastructureError

__all__ = ["fetch_cleared_manifests"]


def fetch_cleared_manifests(candidate_ids: Sequence[str], *, manifests: ManifestSource,
                            holds: HoldSource) -> list[ReleaseManifestPayload]:
    """The cleared manifests of ``candidate_ids``, in request order, or a refusal."""
    if not candidate_ids:
        raise MediaAssemblyError("empty_playlist", "no candidate ids to assemble",
                                 ["candidate_ids"])
    duplicates = sorted(c for c, n in Counter(candidate_ids).items() if n > 1)
    if duplicates:
        raise MediaAssemblyError("duplicate_track", f"requested twice: {duplicates}",
                                 ["candidate_ids"])
    refusals: list[tuple[str, int, str]] = []
    found: list[ReleaseManifestPayload] = []
    for index, candidate_id in enumerate(candidate_ids):
        try:
            manifest = manifests.release_manifest(candidate_id)
            hold_codes = holds.open_hold_codes(recording_subject(candidate_id))
        except ClearanceUnavailableError as exc:
            raise MediaInfrastructureError("clearance_unverifiable", str(exc)) from exc
        if manifest is None:
            refusals.append(("manifest_missing", index, f"{candidate_id}: P5 holds no manifest"))
            continue
        if manifest.candidate_id != candidate_id:
            refusals.append(("manifest_mismatch", index,
                             f"{candidate_id}: P5 answered for {manifest.candidate_id}"))
            continue
        if not manifest.cleared_for_release:
            refusals.append(("track_not_cleared", index,
                             (f"{candidate_id}: disposition {manifest.disposition} "
                              "without an audited override")))
            continue
        if hold_codes:
            refusals.append(("open_hold", index,
                             f"{candidate_id}: open cases {', '.join(hold_codes)}"))
            continue
        found.append(manifest)
    if refusals:
        raise MediaAssemblyError(
            refusals[0][0], "; ".join(detail for _, _, detail in refusals),
            [f"candidate_ids[{index}]" for _, index, _ in refusals])
    return found

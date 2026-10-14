"""Release clearance verified against its source, at the moment of an external write.

P5 is the only authority on whether a recording may be released. A payload that reaches
P7 or P8 carries P5's verdict, but a payload is a claim: it may be stale (a dispute was
raised after the manifest), forged, or replayed. So before any external write, the
publishing phase asks the authorities again:

* P5 must hold a release manifest for the recording (``song_id`` = P4 candidate id) whose
  disposition is cleared and whose canonical artifact is the exact artifact being
  released;
* the exception queue must hold no open case on the recording
  (``recording_subject(candidate_id)``), so a dispute raised after clearance stops the
  write.

Anything that cannot be verified refuses: an unreachable P5 or queue is
``clearance_unverifiable``, never a pass.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import httpx
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import SERVICE_TOKEN_HEADER, Settings
from .exceptions import ExceptionQueue
from .service_urls import service_url
from .temporal import Phase

__all__ = [
    "ClearanceCode",
    "ClearanceReport",
    "ClearanceUnavailableError",
    "ClearanceVerifier",
    "HoldSource",
    "HttpManifestSource",
    "ManifestSource",
    "QueueHoldSource",
    "TrackRef",
    "TrackRefusal",
    "clearance_verifier_for",
    "recording_subject",
    "verify_release_clearance",
]


def recording_subject(candidate_id: str) -> str:
    """The exception-queue subject for one recording, shared by every phase that raises a
    hold against released content (a claim, a dispute, a distributor rejection)."""
    return f"recording/{candidate_id}"


class ClearanceCode(StrEnum):
    MANIFEST_MISSING = "clearance_manifest_missing"
    NOT_CLEARED = "clearance_not_cleared"
    ARTIFACT_MISMATCH = "clearance_artifact_mismatch"
    OPEN_HOLD = "clearance_open_hold"
    UNVERIFIABLE = "clearance_unverifiable"
    NO_TRACKS = "clearance_no_tracks"


class ClearanceUnavailableError(RuntimeError):
    """The authority could not be asked; the caller must refuse, never assume."""


@dataclass(frozen=True, slots=True)
class TrackRef:
    candidate_id: str
    artifact_id: str


@dataclass(frozen=True, slots=True)
class TrackRefusal:
    candidate_id: str
    code: ClearanceCode
    detail: str


@dataclass(frozen=True, slots=True)
class ClearanceReport:
    refusals: tuple[TrackRefusal, ...]

    @property
    def cleared(self) -> bool:
        return not self.refusals

    def summary(self) -> str:
        return "; ".join(f"{r.candidate_id}: {r.code.value} ({r.detail})" for r in self.refusals)


class ManifestSource(Protocol):
    def release_manifest(self, candidate_id: str) -> ReleaseManifestPayload | None:
        """P5's manifest, ``None`` when P5 has none; raises ClearanceUnavailableError."""
        ...


class HoldSource(Protocol):
    def open_hold_codes(self, subject_ref: str) -> tuple[str, ...]:
        """Reason codes of open cases on ``subject_ref``; raises ClearanceUnavailableError."""
        ...


class HttpManifestSource:
    """Asks P5's ``GET /qa/release-manifests/{candidate_id}``."""

    def __init__(self, base_url: str, *, token: str, client: httpx.Client | None = None,
                 timeout_seconds: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def release_manifest(self, candidate_id: str) -> ReleaseManifestPayload | None:
        try:
            response = self._client.get(
                f"{self._base}/qa/release-manifests/{candidate_id}",
                headers={SERVICE_TOKEN_HEADER: self._token},
            )
        except httpx.HTTPError as exc:
            raise ClearanceUnavailableError(f"P5 unreachable: {type(exc).__name__}") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ClearanceUnavailableError(f"P5 answered {response.status_code}")
        try:
            return parse_seam(ReleaseManifestPayload, response.json())
        except (SeamRejection, ValueError) as exc:
            raise ClearanceUnavailableError(f"P5 manifest unreadable: {exc}") from exc


class QueueHoldSource:
    """Reads open cases from the platform exception queue."""

    def __init__(self, session_factory: Callable[[], Session],
                 queue: ExceptionQueue | None = None) -> None:
        self._session_factory = session_factory
        self._queue = queue or ExceptionQueue()

    def open_hold_codes(self, subject_ref: str) -> tuple[str, ...]:
        try:
            with self._session_factory() as session:
                # A hold on a recording may come from any phase, so no origin filter.
                cases = self._queue.list_open(session)
        except SQLAlchemyError as exc:
            raise ClearanceUnavailableError(f"exception queue unreachable: {exc}") from exc
        return tuple(sorted(c.reason_code for c in cases if c.subject_ref == subject_ref))


def verify_release_clearance(tracks: Iterable[TrackRef], *, manifests: ManifestSource,
                             holds: HoldSource) -> ClearanceReport:
    """Every track cleared by P5 for this exact artifact, with no open hold."""
    refs: Sequence[TrackRef] = tuple(tracks)
    if not refs:
        return ClearanceReport((TrackRefusal("", ClearanceCode.NO_TRACKS,
                                             "nothing to verify is not a clearance"),))
    refusals: list[TrackRefusal] = []
    for ref in refs:
        try:
            manifest = manifests.release_manifest(ref.candidate_id)
            hold_codes = holds.open_hold_codes(recording_subject(ref.candidate_id))
        except ClearanceUnavailableError as exc:
            refusals.append(TrackRefusal(ref.candidate_id, ClearanceCode.UNVERIFIABLE, str(exc)))
            continue
        if manifest is None:
            refusals.append(TrackRefusal(ref.candidate_id, ClearanceCode.MANIFEST_MISSING,
                                         "P5 holds no release manifest"))
            continue
        if not manifest.cleared_for_release:
            refusals.append(TrackRefusal(ref.candidate_id, ClearanceCode.NOT_CLEARED,
                                         f"disposition {manifest.disposition}"))
            continue
        if manifest.canonical_artifact_id != ref.artifact_id:
            refusals.append(TrackRefusal(
                ref.candidate_id, ClearanceCode.ARTIFACT_MISMATCH,
                f"P5 cleared {manifest.canonical_artifact_id}, not {ref.artifact_id}",
            ))
            continue
        if hold_codes:
            refusals.append(TrackRefusal(ref.candidate_id, ClearanceCode.OPEN_HOLD,
                                         ", ".join(hold_codes)))
    return ClearanceReport(tuple(refusals))


ClearanceVerifier = Callable[[Sequence[TrackRef]], ClearanceReport]
"""What a publishing phase calls right before an external write."""


def _unverifiable(reason: str) -> ClearanceVerifier:
    def verify(tracks: Sequence[TrackRef]) -> ClearanceReport:
        refs = tuple(tracks) or (TrackRef("", ""),)
        return ClearanceReport(tuple(
            TrackRefusal(ref.candidate_id, ClearanceCode.UNVERIFIABLE, reason) for ref in refs
        ))
    return verify


def clearance_verifier_for(settings: Settings,
                           session_factory: Callable[[], Session]) -> ClearanceVerifier:
    """The deployed verifier: P5 over HTTP (``PLM_SERVICE_URL__QA``) plus the exception
    queue. Without a service token P5 cannot be asked, so every release is refused."""
    if settings.service_token is None:
        return _unverifiable("no service token configured: P5 cannot be asked")
    manifests = HttpManifestSource(service_url(Phase.QA),
                                   token=settings.service_token.get_secret_value())
    holds = QueueHoldSource(session_factory)

    def verify(tracks: Sequence[TrackRef]) -> ClearanceReport:
        return verify_release_clearance(tracks, manifests=manifests, holds=holds)

    return verify

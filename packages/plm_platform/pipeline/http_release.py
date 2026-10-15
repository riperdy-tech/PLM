"""Publication and distribution over HTTP (R5): P6's package to P7 and P8.

* ``upload``: for each media package the run assembled, P6's Seam C6 hop-1 payload
  (``GET /media/packages/{id}/c6``) goes to ``POST /youtube/publish`` on the configured
  channel and operating mode. P7 answers with the publication event (C6 hop 2) and the
  receipts of the writes its guard decided (``external_writes``). A guard refusal is a
  ``409`` (:class:`~.phase_client.ExternalWriteRefused`); a write the guard only recorded
  as intent (a non-live policy for a real provider) is refused the same way, because
  nothing was published.
* ``submit_distribution``: P5's C5 manifest of every track in the package
  (``GET /qa/release-manifests/{candidate_id}``), the C6 package, the publication event
  and the release metadata from configuration go to ``POST /distribution/releases``.
  P8 answers with the release, its ISRCs, the Seam C7 leg-1 distribution record and its
  guard receipts.
* ``await_processing``: ``GET /youtube/attempts/{attempt_id}`` until every publication
  is ``verified`` / ``published`` / ``scheduled`` with its ``published_at``; still
  processing is :class:`~.phase_client.StepNotReady` (retried with backoff), anything
  else fails the step. The run's ``published_at`` is the latest of its units', so the
  outcome horizon has elapsed for every unit when the run matures.

Every receipt is tagged with the unit it belongs to (``media_package_id``), so the
outcome step can attribute each write's cost to its video without an allocation rule.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Final

from ..temporal import Phase, ValidationFailure
from .http_base import HttpStepsBase, instant
from .phase_client import ExternalWriteRefused, StepNotReady, request_key, require
from .types import StepRequest, StepResult

__all__ = ["ReleaseSteps"]

_PUBLISHED_STATES: Final[frozenset[str]] = frozenset({"verified", "published", "scheduled"})
_IN_FLIGHT_STATES: Final[frozenset[str]] = frozenset({
    "received", "locked", "preflight", "ready", "upload_session_creating", "uploading",
    "uploaded", "processing", "metadata_verifying", "thumbnail_pending", "thumbnail_set",
    "playlist_pending", "playlist_attached",
})


def _packages(request: StepRequest) -> list[dict[str, Any]]:
    assembled = require(request.inputs, "assemble", f"{request.step} inputs")
    packages = require(assembled, "packages", "assemble outputs")
    if not isinstance(packages, list) or not packages:
        raise ValidationFailure("assemble outputs: no media packages")
    return [p for p in packages if isinstance(p, dict)]


def _tag(receipts: Any, **unit: str) -> list[dict[str, Any]]:
    if not isinstance(receipts, list):
        return []
    return [{**r, **unit} for r in receipts if isinstance(r, dict)]


class ReleaseSteps(HttpStepsBase):
    # -- upload: P6's C6 package to P7 on the configured channel ---------------------------

    async def upload(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._upload, request)

    def _upload(self, request: StepRequest) -> StepResult:
        publish = self._config.require_publish(request.step)
        key = request.idempotency_key
        p6, p7 = self._client(Phase.MEDIA), self._client(Phase.YOUTUBE)
        publications: list[dict[str, Any]] = []
        receipts: list[dict[str, Any]] = []
        for package in _packages(request):
            media_package_id = str(require(package, "media_package_id", "assemble package"))
            c6 = p6.get(f"/media/packages/{media_package_id}/c6", idempotency_key=key)
            path = "/youtube/publish"
            answer = p7.post(path, idempotency_key=request_key(key, "publish", media_package_id),
                             body={"media_package": c6, "channel_id": publish.channel_id,
                                   "operating_mode": publish.operating_mode})
            writes = _tag(answer.get("external_writes"), media_package_id=media_package_id)
            event = answer.get("publication_event")
            if not isinstance(event, dict):
                intent = next((w for w in writes if w.get("decision") == "record_intent"), None)
                if intent is not None:
                    raise ExternalWriteRefused(
                        Phase.YOUTUBE, path, 409, "external_write_record_intent",
                        f"{intent.get('capability')} not performed: {intent.get('reason')}")
                raise ValidationFailure(
                    f"P7 did not publish {media_package_id}: state {answer.get('state')!r}, "
                    f"error {answer.get('error')!r}")
            publications.append({
                "media_package_id": media_package_id,
                "p3_package_id": package.get("p3_package_id"),
                "attempt_id": str(require(answer, "attempt_id", "P7 publish")),
                "youtube_video_id": str(require(event, "youtube_video_id", "P7 event")),
                "channel_id": str(require(event, "channel_id", "P7 event")),
                "published_at": str(require(event, "published_at", "P7 event")),
                "publication_event": event,
            })
            receipts.extend(writes)
        return StepResult(
            outputs={"publications": publications, "external_writes": receipts},
            provider_ids={f"youtube_video.{n}": p["youtube_video_id"]
                          for n, p in enumerate(publications)},
        )

    # -- submit_distribution: C5 manifests + C6 package + publication event to P8 ----------

    async def submit_distribution(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._submit_distribution, request)

    def _submit_distribution(self, request: StepRequest) -> StepResult:
        release = self._config.require_release(request.step)
        key = request.idempotency_key
        uploaded = require(request.inputs, "upload", "submit_distribution inputs")
        publications = require(uploaded, "publications", "upload outputs")
        if not isinstance(publications, list) or not publications:
            raise ValidationFailure("upload outputs: no publications")
        release_date = (request.requested_at
                        + timedelta(days=release.release_date_offset_days)).date().isoformat()
        p5, p6, p8 = (self._client(Phase.QA), self._client(Phase.MEDIA),
                      self._client(Phase.DISTRIBUTION))
        releases: list[dict[str, Any]] = []
        receipts: list[dict[str, Any]] = []
        for publication in publications:
            media_package_id = str(require(publication, "media_package_id", "publication"))
            c6 = p6.get(f"/media/packages/{media_package_id}/c6", idempotency_key=key)
            tracklist = require(c6, "tracklist", f"C6 package {media_package_id}")
            manifests = [
                p5.get(f"/qa/release-manifests/{require(track, 'song_id', 'C6 track')}",
                       idempotency_key=key)
                for track in tracklist
            ]
            answer = p8.post(
                "/distribution/releases",
                idempotency_key=request_key(key, "submit", media_package_id),
                body={"track_manifests": manifests, "media_package": c6,
                      "publication": require(publication, "publication_event", "publication"),
                      "artist_name": release.artist_name, "release_date": release_date,
                      "label_name": release.label_name,
                      "copyright_line": release.copyright_line,
                      "publisher_line": release.publisher_line,
                      "territories": release.territories})
            record = require(answer, "distribution_record", "P8 release")
            isrcs = require(answer, "isrcs", "P8 release")
            if not isinstance(isrcs, list) or not isrcs:
                raise ValidationFailure(f"P8 release for {media_package_id} names no ISRC")
            releases.append({
                "media_package_id": media_package_id,
                "release_id": str(require(answer, "release_id", "P8 release")),
                "status": str(require(answer, "status", "P8 release")),
                "isrcs": [str(i) for i in isrcs],
                "distribution_record": record,
            })
            receipts.extend(_tag(answer.get("external_writes"),
                                 media_package_id=media_package_id))
        return StepResult(
            outputs={"releases": releases, "external_writes": receipts},
            provider_ids={f"release.{n}": r["release_id"] for n, r in enumerate(releases)},
        )

    # -- await_processing: P7 reports every publication processed --------------------------

    async def await_processing(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._await_processing, request)

    def _await_processing(self, request: StepRequest) -> StepResult:
        key = request.idempotency_key
        uploaded = require(request.inputs, "upload", "await_processing inputs")
        publications = require(uploaded, "publications", "upload outputs")
        if not isinstance(publications, list) or not publications:
            raise ValidationFailure("upload outputs: no publications")
        p7 = self._client(Phase.YOUTUBE)
        units: list[dict[str, Any]] = []
        for publication in publications:
            attempt_id = str(require(publication, "attempt_id", "publication"))
            status = p7.get(f"/youtube/attempts/{attempt_id}", idempotency_key=key)
            state = str(require(status, "state", f"P7 attempt {attempt_id}"))
            if state in _IN_FLIGHT_STATES:
                raise StepNotReady(f"P7 attempt {attempt_id} is still {state}",
                                   code="youtube_processing")
            if state not in _PUBLISHED_STATES:
                raise ValidationFailure(
                    f"P7 attempt {attempt_id} ended {state}: {status.get('error_code')} "
                    f"{status.get('error_message')}")
            published_at = instant(require(status, "published_at", f"P7 attempt {attempt_id}"),
                                   f"P7 attempt {attempt_id} published_at")
            units.append({"media_package_id": publication.get("media_package_id"),
                          "youtube_video_id": str(require(status, "youtube_video_id",
                                                          f"P7 attempt {attempt_id}")),
                          "attempt_id": attempt_id, "state": state,
                          "published_at": published_at.isoformat()})
        latest = max(instant(u["published_at"], "unit published_at") for u in units)
        return StepResult(
            outputs={"published_at": latest.isoformat(), "units": units},
            provider_ids={f"youtube_video.{n}": u["youtube_video_id"]
                          for n, u in enumerate(units)},
        )

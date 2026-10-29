"""Phase steps over the phase HTTP APIs (R3–R5).

:class:`HttpPhaseSteps` carries every step of the PART XIV pipeline over HTTP, with the
service token and an ``Idempotency-Key`` per write (:mod:`.phase_client`):

* ``research``: P2's queued directive -> P3 experiment design.
* ``policy_check``: P3 preflight validation, then one package per treatment.
* ``generate``: P4 plan + execute per track spec, each candidate exported to P5. Only
  the given ``track_spec_ids`` when the step is a playlist top-up.
* ``qa``: P4's C4 event per candidate, evaluated by P5 from the artifact store.
* ``assemble``: P6 assembles each P3 package's cleared recordings into a playlist whose
  length lies in the package's declared ``duration_range_seconds``. A package whose
  cleared supply is short of the minimum (P6 ``playlist_supply_short``) is reported in
  one :class:`PlaylistSupplyShort` with the missing seconds, after every other package
  was assembled; the workflow then tops the short ones up.
* ``extend``: P3 adds track specs to a short package (``POST .../packages/{id}/extend``).
* ``upload``, ``submit_distribution``, ``await_processing``: :mod:`.http_release`.
* ``evaluate_outcome``, ``dispatch_reward``, ``learn``: :mod:`.http_outcome`.

P3 and P4 deduplicate on ``Idempotency-Key`` (R5), so a retried step replays its writes;
each write inside a step has its own key (:func:`~.phase_client.request_key`).
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping
from typing import Any, Final

from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload

from ..temporal import (
    CreativeRejection,
    Phase,
    PolicyRejection,
    ValidationFailure,
)
from .config import PipelineConfig
from .http_outcome import OutcomeSteps
from .http_release import ReleaseSteps
from .phase_client import (
    ExternalWriteRefused,
    PhaseClient,
    PhaseRequestFailed,
    PhaseRequestRefused,
    StepNotReady,
    request_key,
    require,
)
from .types import StepRequest, StepResult

__all__ = [
    "PLAYLIST_SUPPLY_SHORT_CODE",
    "ExternalWriteRefused",
    "HttpPhaseSteps",
    "PhaseClient",
    "PhaseRequestFailed",
    "PhaseRequestRefused",
    "PlaylistSupplyShort",
    "StepNotReady",
]

PLAYLIST_SUPPLY_SHORT_CODE: Final[str] = "playlist_supply_short"
"""P6's refusal when every cleared recording together is shorter than the range minimum."""

_MISSING_SECONDS: Final = re.compile(r"(\d+(?:\.\d+)?)\s*s(?:econds)?\s+(?:are\s+)?missing")


class PlaylistSupplyShort(ValidationFailure):
    """At least one playlist's cleared recordings do not reach its declared minimum.

    ``details["short_packages"]``: one ``{p3_package_id, missing_seconds,
    shortest_track_seconds, track_spec_ids}`` per short package; the packages that were
    assembled are in ``details["assembled"]``. Nothing was published.
    """

    code = PLAYLIST_SUPPLY_SHORT_CODE
    no_effect = True

    def __init__(self, short: list[dict[str, Any]], assembled: list[dict[str, Any]]) -> None:
        self.details: dict[str, Any] = {"short_packages": short, "assembled": assembled}
        listed = ", ".join(f"{s['p3_package_id']} ({s['missing_seconds']}s missing)"
                           for s in short)
        super().__init__(f"playlist supply short of the declared minimum: {listed}")


def _missing_seconds(refusal: PhaseRequestRefused) -> int:
    """P6's ``detail.facts.missing_seconds`` (R5), never guessed."""
    facts = refusal.body.get("facts")
    for source in (facts if isinstance(facts, dict) else {}, refusal.body):
        value = source.get("missing_seconds")
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return math.ceil(value)
    match = _MISSING_SECONDS.search(refusal.message)
    if match:
        return math.ceil(float(match.group(1)))
    raise ValidationFailure(
        f"P6 refused {PLAYLIST_SUPPLY_SHORT_CODE} without the missing seconds; the top-up "
        f"cannot be sized: {refusal}")


_QA_PASSING: Final[frozenset[str]] = frozenset({"PASSED", "PASSED_WITH_WARNINGS"})
_QA_DISPOSITIONS: Final[frozenset[str]] = _QA_PASSING | {"REJECTED", "QUARANTINED"}


def _qa_evaluation(candidate_id: str, evaluated: Any) -> dict[str, Any]:
    """One P5 answer, checked: a disposition without the facts it implies is no answer."""
    where = f"P5 evaluation of {candidate_id}"
    disposition = str(require(evaluated, "disposition", where))
    if disposition not in _QA_DISPOSITIONS:
        raise ValidationFailure(f"{where}: unknown disposition {disposition!r}")
    record: dict[str, Any] = {
        "candidate_id": candidate_id,
        "evaluation_id": str(require(evaluated, "evaluation_id", where)),
        "disposition": disposition,
        "release_manifest_id": None,
        "quarantine_case_id": None,
        "reason_codes": [],
    }
    if disposition in _QA_PASSING:
        # A pass P5 issued no release manifest for is not a pass.
        record["release_manifest_id"] = str(require(evaluated, "release_manifest_id", where))
    elif disposition == "QUARANTINED":
        record["quarantine_case_id"] = str(require(evaluated, "quarantine_case_id", where))
        codes = require(evaluated, "quarantine_reason_codes", where)
        if not isinstance(codes, list) or not codes or not all(
                isinstance(c, str) and c for c in codes):
            raise ValidationFailure(f"{where}: quarantine reason codes {codes!r} are invalid")
        record["reason_codes"] = list(codes)
    return record


def _qa_result(evaluations: list[dict[str, Any]]) -> StepResult:
    if not evaluations:
        raise ValidationFailure("qa: generate produced no candidates to evaluate")
    passed = [e for e in evaluations if e["disposition"] in _QA_PASSING]
    quarantined = [e for e in evaluations if e["disposition"] == "QUARANTINED"]
    holds: list[dict[str, Any]] = []
    if passed:
        disposition = "PASSED"
    elif quarantined:
        disposition = "QUARANTINED"
        holds = [
            {"reason_code": code,
             "details": {"candidate_id": e["candidate_id"], "evaluation_id": e["evaluation_id"],
                         "quarantine_case_id": e["quarantine_case_id"]}}
            for e in quarantined for code in e["reason_codes"]
        ]
    else:
        disposition = "REJECTED"
    return StepResult(
        outputs={
            "disposition": disposition,
            "holds": holds,
            "evaluations": evaluations,
            "passed_candidate_ids": [e["candidate_id"] for e in passed],
            "release_manifest_ids": [e["release_manifest_id"] for e in passed],
        },
        provider_ids={f"evaluation.{n}": e["evaluation_id"] for n, e in enumerate(evaluations)},
    )


class HttpPhaseSteps(ReleaseSteps, OutcomeSteps):
    """:class:`~plm_platform.pipeline.activities.PhaseSteps` over the phase APIs."""

    def __init__(self, clients: Mapping[Phase, PhaseClient], *,
                 config: PipelineConfig | None = None) -> None:
        super().__init__(clients, config=config)

    # -- research: P2 directive -> P3 experiment design -------------------------------------

    async def research(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._research, request)

    def _research(self, request: StepRequest) -> StepResult:
        key = request.idempotency_key
        directives = self._client(Phase.AR).get(
            "/ar/directives", idempotency_key=key, params={"brand_id": request.brand_id})
        if not isinstance(directives, list):
            raise ValidationFailure("P2 /ar/directives did not return a list")
        directive = next((d for d in directives if isinstance(d, dict)
                          and d.get("directive_id") == request.directive_id), None)
        if directive is None:
            raise PolicyRejection(
                f"directive {request.directive_id!r} is not queued for brand "
                f"{request.brand_id!r} at P2 (expired, consumed or unknown)"
            )
        horizon = require(directive, "outcome_horizon", "P2 directive")
        created = self._client(Phase.EXPERIMENTS).post(
            "/experiments/v1/experiments", idempotency_key=request_key(key, "design"),
            body={"directive": directive})
        experiment = require(created, "experiment", "P3 create experiment")
        experiment_id = str(require(experiment, "experiment_id", "P3 experiment"))
        treatments = [str(require(t, "treatment_id", "P3 treatment"))
                      for t in experiment.get("treatments") or []]
        if not treatments:
            raise ValidationFailure(f"P3 experiment {experiment_id} has no treatments")
        return StepResult(
            outputs={"experiment_id": experiment_id, "treatment_ids": treatments,
                     "outcome_horizon": str(horizon),
                     "arm_id": str(require(directive, "arm_id", "P2 directive")),
                     "decision_id": str(require(directive, "decision_id", "P2 directive"))},
            provider_ids={"experiment_id": experiment_id},
        )

    # -- policy check: P3 preflight validation, then one package per treatment -------------

    async def policy_check(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._policy_check, request)

    def _policy_check(self, request: StepRequest) -> StepResult:
        key = request.idempotency_key
        research = require(request.inputs, "research", "policy_check inputs")
        experiment_id = str(require(research, "experiment_id", "research outputs"))
        treatments = require(research, "treatment_ids", "research outputs")
        p3 = self._client(Phase.EXPERIMENTS)
        validated = p3.post(f"/experiments/v1/experiments/{experiment_id}/validate",
                            idempotency_key=request_key(key, "validate"))
        report = require(validated, "report", "P3 validate")
        if report.get("passed") is not True:
            raise PolicyRejection(f"P3 preflight did not pass for {experiment_id}: {report}")
        packages: list[str] = []
        for treatment_id in treatments:
            materialized = p3.post(
                f"/experiments/v1/experiments/{experiment_id}/materialize",
                idempotency_key=request_key(key, "materialize", str(treatment_id)),
                body={"treatment_id": treatment_id, "brand_id": request.brand_id})
            package = require(materialized, "package", "P3 materialize")
            packages.append(str(require(package, "package_id", "P3 package")))
        return StepResult(
            outputs={"experiment_id": experiment_id, "validated": True,
                     "package_ids": packages},
            provider_ids={f"package.{n}": pid for n, pid in enumerate(packages)},
        )

    # -- generate: P4 plan + execute per track spec, export each candidate to P5 -----------

    async def generate(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._generate, request)

    def _generate(self, request: StepRequest) -> StepResult:
        """Generate every track spec of the packages, or only ``track_spec_ids`` (a top-up).

        P4's guard decides each generation (``music.generate``); its receipts come back
        on the execute answer as ``external_writes`` and are tagged with the P3 package.
        An execute answer without them is recorded as an ``unreported`` write, whose
        cost is then unknown (never zero).
        """
        key = request.idempotency_key
        policy = require(request.inputs, "policy_check", "generate inputs")
        package_ids = require(policy, "package_ids", "policy_check outputs")
        only = request.inputs.get("track_spec_ids")
        wanted = None if only is None else {str(s) for s in only}
        p3, p4 = self._client(Phase.EXPERIMENTS), self._client(Phase.MUSIC)
        provider_ids: dict[str, str] = {}
        candidates: list[str] = []
        plans: list[str] = []
        receipts: list[dict[str, Any]] = []
        by_package: dict[str, list[str]] = {}
        # Do not export *any* C4 candidate until every P4 plan confirms
        # completion. A partial plan may retain useful audio in P4, but must
        # not be mistaken for successful completion of the generating step.
        pending_exports: list[str] = []
        for package_id in package_ids:
            package = p3.get(f"/experiments/v1/packages/{package_id}", idempotency_key=key)
            for spec in require(package, "track_specs", f"P3 package {package_id}"):
                spec_id = str(require(spec, "track_spec_id", f"P3 package {package_id}"))
                if wanted is not None and spec_id not in wanted:
                    continue
                plan = p4.post("/music/plans", idempotency_key=request_key(key, "plan", spec_id),
                               body=spec)
                plan_id = str(require(plan, "plan_id", "P4 plan"))
                plans.append(plan_id)
                execute_key = request_key(key, "execute", plan_id)
                run = p4.post(f"/music/plans/{plan_id}/execute", idempotency_key=execute_key,
                              body=spec)
                writes = run.get("external_writes")
                missing_receipt = not isinstance(writes, list) or not writes
                receipt_keys: set[tuple[str, str]] = set()
                if isinstance(writes, list):
                    for write in writes:
                        if isinstance(write, dict) and all(
                            isinstance(write.get(field), str) and write[field].strip()
                            for field in ("capability", "idempotency_key", "decision")
                        ):
                            receipt_key = (write["capability"], write["idempotency_key"])
                            if receipt_key in receipt_keys:
                                # Duplicate ledger keys cannot prove additional
                                # attempts and must not be counted twice as spend.
                                missing_receipt = True
                                continue
                            receipt_keys.add(receipt_key)
                            receipts.append({**write, "p3_package_id": str(package_id)})
                        else:
                            # Never silently drop an invalid receipt: the omitted
                            # paid attempt would otherwise appear to cost nothing.
                            missing_receipt = True
                # The real P4 endpoint reports attempts_count for this execution.
                # Fewer distinct guard receipts than attempted provider calls
                # means some costs cannot be verified, even if the supplied
                # receipts are individually well-formed.
                count = run.get("attempts_count")
                count_gap = (
                    count is not None
                    and (type(count) is not int or count < 0 or count > len(receipt_keys))
                )
                if count_gap:
                    missing_receipt = True
                if missing_receipt:
                    receipts.append({
                        "capability": "music.generate",
                        "idempotency_key": execute_key,
                        "decision": "unreported",
                        "reason": (
                            "p4_attempt_count_receipt_mismatch"
                            if count_gap else (
                                "p4_reported_invalid_guard_receipt"
                                if isinstance(writes, list) and writes
                                else "p4_reported_no_guard_receipt"
                            )
                        ),
                        "p3_package_id": str(package_id),
                    })
                outcome = require(run, "status", f"P4 plan {plan_id} execute")
                if outcome == "failed":
                    # HTTP 200 can still mean P4 completed only part of its C3
                    # candidate target. Retain the provider's recorded attempt
                    # and funds in P4; do not export any C4 event or continue QA.
                    raise CreativeRejection(
                        f"P4 plan {plan_id} failed its candidate target; "
                        f"produced {len(run.get('candidates') or [])} candidates "
                        f"and reported errors {run.get('errors')!r}"
                    )
                if outcome != "completed":
                    raise ValidationFailure(
                        f"P4 plan {plan_id}: unexpected execute status {outcome!r}"
                    )
                plan_candidates = require(run, "candidates", f"P4 plan {plan_id} execute")
                if not isinstance(plan_candidates, list):
                    raise ValidationFailure(
                        f"P4 plan {plan_id}: completed plan returned a non-list candidate set"
                    )
                if not plan_candidates:
                    # One empty completed plan must not be masked by another
                    # successful plan in the same multi-track generation step.
                    raise CreativeRejection(
                        f"P4 plan {plan_id}: completed plan returned zero candidates"
                    )
                for candidate in plan_candidates:
                    candidate_id = require(candidate, "candidate_id", f"P4 plan {plan_id} candidate")
                    if not isinstance(candidate_id, str) or not candidate_id.strip():
                        raise ValidationFailure(
                            f"P4 plan {plan_id}: candidate_id must be a nonempty string"
                        )
                    if candidate_id in candidates:
                        raise ValidationFailure(
                            f"P4 plan {plan_id}: duplicate candidate_id {candidate_id!r}"
                        )
                    pending_exports.append(candidate_id)
                    candidates.append(candidate_id)
                    by_package.setdefault(str(package_id), []).append(candidate_id)
        for n, plan_id in enumerate(plans):
            provider_ids[f"plan.{n}"] = plan_id
        for n, candidate_id in enumerate(candidates):
            provider_ids[f"candidate.{n}"] = candidate_id
        if not candidates:
            # Provider calls were made and may have spent credits: not a no-effect failure.
            raise CreativeRejection(f"P4 produced no candidates for plans {plans}")
        for candidate_id in pending_exports:
            p4.post(f"/music/candidates/{candidate_id}/export-to-p5",
                    idempotency_key=request_key(key, "export", candidate_id))
        return StepResult(
            outputs={"plan_ids": plans, "candidate_ids": candidates,
                     "candidates_by_package": by_package, "external_writes": receipts},
            provider_ids=provider_ids,
            cost=None,  # P4 reports provider credits, not money; the ledger holds the spend
        )

    # -- qa: P4's C4 event per candidate, evaluated by P5 from the artifact store (R4) -----

    async def qa(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._qa, request)

    def _qa(self, request: StepRequest) -> StepResult:
        """Evaluate every generated candidate and return one disposition for the run.

        P4's export answers with the candidate's C4 event (re-exporting is idempotent);
        P5 reads the audio by the event's URI and sha256. Any refusal, such as an
        artifact that is missing or fails its checksum, ends the step; nothing is
        skipped. The run's disposition: ``PASSED`` when at least one candidate passed
        (with or without warnings, and with its release manifest); otherwise
        ``QUARANTINED`` with one hold per quarantine reason code; otherwise
        ``REJECTED``. Quarantine cases of candidates not carried forward stay open in P5.
        """
        key = request.idempotency_key
        generated = require(request.inputs, "generate", "qa inputs")
        candidate_ids = require(generated, "candidate_ids", "generate outputs")
        if not isinstance(candidate_ids, list):
            raise ValidationFailure("generate outputs: 'candidate_ids' is not a list")
        p4, p5 = self._client(Phase.MUSIC), self._client(Phase.QA)
        evaluations: list[dict[str, Any]] = []
        for candidate_id in candidate_ids:
            exported = p4.post(f"/music/candidates/{candidate_id}/export-to-p5",
                               idempotency_key=request_key(key, "export", str(candidate_id)))
            event = require(exported, "event", f"P4 export of {candidate_id}")
            evaluated = p5.post("/qa/evaluations",
                                idempotency_key=request_key(key, "evaluate", str(candidate_id)),
                                body={"candidate_event": event})
            evaluations.append(_qa_evaluation(str(candidate_id), evaluated))
        return _qa_result(evaluations)

    # -- assemble: P6 assembles and renders each P3 package's cleared recordings -----------

    ASSEMBLE_TIMEOUT_SECONDS: Final[float] = 3300.0
    """A bounded, long P6 render call with headroom under the 3600s default
    activity limit. Multiple packages or overriden shorter activity timeouts
    still require a durable render job; this is not a recovery guarantee.
    """

    async def assemble(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._assemble, request)

    @staticmethod
    def _playlist_facts(package: Any, package_id: str) -> dict[str, Any]:
        """P3's playlist, visual and title facts for P6, never defaulted.

        ``duration_range_seconds`` is the playlist spec's declared range (owner decision
        2026-10-15: a 45–75 min playlist is a range, not a length); ``visual_profile`` the
        visual spec's visual world; ``playlist_concept`` the title spec's formatting
        pattern filled with the first track spec's genre, mood and use case (P3's declared
        title rule over P3's own facts). ``shortest_track_seconds`` is the smallest
        declared track minimum, which sizes a top-up without overestimating a track.
        """
        where = f"P3 package {package_id}"
        playlist = require(package, "playlist_spec", where)
        visual = require(package, "visual_spec", where)
        title = require(package, "title_spec", where)
        specs = require(package, "track_specs", where)
        if not isinstance(specs, list) or not specs:
            raise ValidationFailure(f"{where}: no track specs")
        first = specs[0]
        preferences = require(first, "bounded_preferences", f"{where} track spec")
        constraints = require(first, "hard_constraints", f"{where} track spec")
        moods = require(preferences, "mood", f"{where} preferences")
        uses = require(preferences, "use_case", f"{where} preferences")
        if not isinstance(moods, list) or not moods or not isinstance(uses, list) or not uses:
            raise ValidationFailure(f"{where}: mood and use case are needed for the concept")
        pattern = str(require(title, "formatting_pattern", f"{where} title spec"))
        try:
            concept = pattern.format_map({
                "mood": moods[0], "use_case": uses[0],
                "genre": require(constraints, "genre", f"{where} constraints")})
        except (KeyError, IndexError, ValueError) as exc:
            raise ValidationFailure(
                f"{where}: title pattern {pattern!r} needs a fact P3 does not give: {exc}"
            ) from exc
        duration = require(playlist, "duration_range_seconds", f"{where} playlist spec")
        minimum = require(duration, "min_seconds", f"{where} duration range")
        maximum = require(duration, "max_seconds", f"{where} duration range")
        track_minimums = [require(require(s, "duration_range", f"{where} track spec"),
                                  "min_seconds", f"{where} track duration range")
                          for s in specs]
        return {
            "brand_id": str(require(package, "brand_id", where)),
            "duration_range_seconds": {"min_seconds": int(minimum),
                                       "max_seconds": int(maximum)},
            "visual_profile": str(require(visual, "visual_world", f"{where} visual spec")),
            "playlist_concept": concept,
            "track_spec_ids": {str(require(s, "track_spec_id", where)) for s in specs},
            "shortest_track_seconds": int(min(track_minimums)),
        }

    def _cleared_track_specs(self, candidate_ids: list[Any], key: str) -> dict[str, str]:
        """``candidate_id -> track_spec_id`` for every candidate P5 cleared for release.

        P5 is asked for each candidate's manifest: none (404) or not cleared means the
        candidate is not offered to P6. P6 re-verifies every id it is sent.
        """
        p5 = self._client(Phase.QA)
        cleared: dict[str, str] = {}
        for raw in candidate_ids:
            candidate_id = str(raw)
            try:
                body = p5.get(f"/qa/release-manifests/{candidate_id}", idempotency_key=key)
            except PhaseRequestRefused as exc:
                if exc.status == 404:
                    continue
                raise
            try:
                manifest = parse_seam(ReleaseManifestPayload, body)
            except SeamRejection as exc:
                raise ValidationFailure(
                    f"P5 manifest of {candidate_id} is unreadable: {exc}") from exc
            if manifest.cleared_for_release:
                cleared[candidate_id] = manifest.track_spec_id
        return cleared

    def _assemble(self, request: StepRequest) -> StepResult:
        """One media package per P3 package, from its cleared recordings.

        Inputs: ``policy_check.package_ids`` (P3 packages) and ``generate.candidate_ids``
        (every candidate so far, top-ups included). Each package's cleared candidates go
        to ``POST /media/packages/assemble`` by id with the package's playlist facts; P6
        fetches the manifests from P5 itself. A package with no cleared recording, or one
        P6 refuses as ``playlist_supply_short``, is short by its missing seconds: the
        step assembles the others, then raises :class:`PlaylistSupplyShort` so the
        workflow can top the short ones up. A package whose render post-render QA
        blocked fails the step with P6's reason codes; any other P6 refusal fails it
        with P6's code.
        """
        key = request.idempotency_key
        policy = require(request.inputs, "policy_check", "assemble inputs")
        package_ids = require(policy, "package_ids", "policy_check outputs")
        generated = require(request.inputs, "generate", "assemble inputs")
        candidate_ids = require(generated, "candidate_ids", "generate outputs")
        if not isinstance(package_ids, list) or not isinstance(candidate_ids, list):
            raise ValidationFailure("assemble inputs: package and candidate ids must be lists")
        cleared = self._cleared_track_specs(candidate_ids, key)
        p3, p6 = self._client(Phase.EXPERIMENTS), self._client(Phase.MEDIA)
        packages: list[dict[str, Any]] = []
        short: list[dict[str, Any]] = []
        for raw_package_id in package_ids:
            package_id = str(raw_package_id)
            facts = self._playlist_facts(
                p3.get(f"/experiments/v1/packages/{package_id}", idempotency_key=key),
                package_id)
            shortage = {"p3_package_id": package_id,
                        "shortest_track_seconds": facts["shortest_track_seconds"],
                        "track_spec_ids": sorted(facts["track_spec_ids"])}
            members = [cid for cid, spec in cleared.items() if spec in facts["track_spec_ids"]]
            if not members:
                short.append({**shortage, "cleared_count": 0, "missing_seconds":
                              facts["duration_range_seconds"]["min_seconds"]})
                continue
            try:
                assembled = p6.post(
                    "/media/packages/assemble",
                    idempotency_key=request_key(key, "assemble", package_id),
                    timeout_seconds=self.ASSEMBLE_TIMEOUT_SECONDS,
                    body={"candidate_ids": members, "brand_id": facts["brand_id"],
                          "duration_range_seconds": facts["duration_range_seconds"],
                          "visual_profile": facts["visual_profile"],
                          "playlist_concept": facts["playlist_concept"]})
            except PhaseRequestRefused as exc:
                if exc.code != PLAYLIST_SUPPLY_SHORT_CODE:
                    raise
                short.append({**shortage, "cleared_count": len(members),
                              "missing_seconds": _missing_seconds(exc)})
                continue
            media_package_id = str(require(assembled, "media_package_id", "P6 package"))
            package_status = str(require(assembled, "status", "P6 package"))
            if package_status != "ready_for_publish":
                codes = assembled.get("blocking_reason_codes") or []
                raise ValidationFailure(
                    f"P6 package {media_package_id} is {package_status}: post-render QA "
                    f"blocked it ({', '.join(map(str, codes)) or 'no reason codes'})")
            packages.append({"media_package_id": media_package_id, "p3_package_id": package_id,
                             "candidate_ids": members,
                             "video_checksum": require(assembled, "video_checksum",
                                                       "P6 package")})
        if short:
            raise PlaylistSupplyShort(short, packages)
        if not packages:
            raise ValidationFailure(f"assemble: no P3 package in {package_ids}")
        return StepResult(
            outputs={"media_package_ids": [p["media_package_id"] for p in packages],
                     "packages": packages},
            provider_ids={f"media_package.{n}": p["media_package_id"]
                          for n, p in enumerate(packages)},
        )

    # -- extend: P3 tops a short package up with more track specs --------------------------

    async def extend(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._extend, request)

    def _extend(self, request: StepRequest) -> StepResult:
        """``POST /experiments/v1/packages/{id}/extend {count, idempotency_key}``.

        The body's key is the step's key, so a replay returns the same specs. P3's 409
        ``package_extension_limit_exceeded`` (its declared top-up bound) is a refusal the
        workflow turns into the ``PLAYLIST_SUPPLY_SHORT`` hold.
        """
        package_id = str(require(request.inputs, "p3_package_id", "extend inputs"))
        count = require(request.inputs, "count", "extend inputs")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValidationFailure(f"extend inputs: count {count!r} must be a positive integer")
        answer = self._client(Phase.EXPERIMENTS).post(
            f"/experiments/v1/packages/{package_id}/extend",
            idempotency_key=request.idempotency_key,
            body={"count": count, "idempotency_key": request.idempotency_key})
        specs = require(answer, "track_specs", "P3 extend")
        spec_ids = [str(require(s, "track_spec_id", "P3 extended spec")) for s in specs]
        if not spec_ids:
            raise ValidationFailure(f"P3 extended {package_id} with no track spec")
        return StepResult(
            outputs={"p3_package_id": package_id, "count": count,
                     "track_spec_ids": spec_ids,
                     "extension_id": answer.get("extension_id"),
                     "remaining_extension_track_count":
                         answer.get("remaining_extension_track_count")},
            provider_ids={"extension_id": str(answer.get("extension_id") or package_id)},
        )

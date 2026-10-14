"""Phase steps over the phase HTTP APIs (R3), and the small client they share.

:class:`PhaseClient` sends every request with the service token
(``X-PLM-Service-Token``) and the step's ``Idempotency-Key``, and classifies the answer:

* 2xx: the JSON body.
* A definite refusal (any other 4xx, with the receiver's ErrorShape ``code``):
  :class:`PhaseRequestRefused`, non-retryable; the request itself must change.
* Anything that may clear by itself (connection refused, timeouts, 5xx, and 401 / 403 /
  408 / 425 / 429 as in :mod:`plm_platform.outbox`): :class:`PhaseRequestFailed`,
  retried by the Temporal retry policy.

:class:`HttpPhaseSteps` implements a step over HTTP only where today's endpoints exist
and carry complete facts: P2 -> P3 design (``research``), P3 validate + materialize
(``policy_check``), P4 plan + execute + export to P5 (``generate``), and P4's C4 event
evaluated by P5, which reads the audio by URI and checksum (``qa``, R4). Every other step
raises :class:`~plm_platform.temporal.CapabilityUnavailable` naming the stage that
delivers it; the workflow turns that into a hold. Nothing is faked.

Known gap, recorded for R5: P3 and P4 do not yet dedupe on ``Idempotency-Key``, so a
retry after a lost response can be refused as a duplicate (P3) or fail on P4's
deterministic plan id; the step then fails closed rather than creating a second effect.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, Final

import httpx
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload

from ..config import SERVICE_TOKEN_HEADER, SERVICE_TOKEN_VAR
from ..outbox import IDEMPOTENCY_KEY_HEADER
from ..temporal import (
    CapabilityUnavailable,
    CreativeRejection,
    Phase,
    PolicyRejection,
    ValidationFailure,
)
from .states import Step
from .types import StepRequest, StepResult

__all__ = [
    "UNAVAILABLE_STEPS",
    "HttpPhaseSteps",
    "PhaseClient",
    "PhaseRequestFailed",
    "PhaseRequestRefused",
]

_DETAIL_LIMIT: Final[int] = 512
_RETRYABLE_4XX: Final[frozenset[int]] = frozenset({401, 403, 408, 425, 429})


class PhaseRequestRefused(ValidationFailure):
    """The phase definitely refused the request (a 4xx with its reason code)."""

    def __init__(self, phase: Phase, path: str, status: int, code: str, message: str) -> None:
        self.phase = phase
        self.status = status
        self.code = code
        super().__init__(f"{phase.name} {path} refused ({status} {code}): {message}")


class PhaseRequestFailed(RuntimeError):
    """The request may succeed later (transport failure, 5xx, auth or rate limit)."""

    def __init__(self, phase: Phase, path: str, code: str, message: str) -> None:
        self.phase = phase
        self.code = code
        super().__init__(f"{phase.name} {path} failed ({code}): {message}")


def _error_shape(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return f"http_{response.status_code}", response.text[:_DETAIL_LIMIT]
    shape = body.get("detail", body) if isinstance(body, dict) else None
    if isinstance(shape, dict) and isinstance(shape.get("code"), str):
        return shape["code"], str(shape.get("message", ""))[:_DETAIL_LIMIT]
    if isinstance(shape, str):
        return f"http_{response.status_code}", shape[:_DETAIL_LIMIT]
    return f"http_{response.status_code}", json.dumps(body)[:_DETAIL_LIMIT]


class PhaseClient:
    """A synchronous client for one phase API (``client`` owns base URL and timeout)."""

    def __init__(self, phase: Phase, client: httpx.Client, *, token: str | None) -> None:
        self.phase = phase
        self._client = client
        self._token = token

    def get(self, path: str, *, idempotency_key: str,
            params: Mapping[str, str] | None = None) -> Any:
        return self._request("GET", path, idempotency_key=idempotency_key, params=params)

    def post(self, path: str, *, idempotency_key: str, body: Any = None,
             timeout_seconds: float | None = None) -> Any:
        """``timeout_seconds`` overrides the client's timeout for a long call (a render)."""
        return self._request("POST", path, idempotency_key=idempotency_key, body=body,
                             timeout_seconds=timeout_seconds)

    def _request(self, method: str, path: str, *, idempotency_key: str,
                 params: Mapping[str, str] | None = None, body: Any = None,
                 timeout_seconds: float | None = None) -> Any:
        if not self._token:
            raise PhaseRequestRefused(
                self.phase, path, 503, "blocked_pending_credentials",
                f"no service token: set {SERVICE_TOKEN_VAR} for the pipeline worker",
            )
        if not idempotency_key.strip():
            raise ValueError("every phase request carries an idempotency key")
        headers = {SERVICE_TOKEN_HEADER: self._token, IDEMPOTENCY_KEY_HEADER: idempotency_key}
        try:
            response = self._client.request(
                method, path, headers=headers,
                params=None if params is None else dict(params),
                json=body if method == "POST" else None,
                timeout=(httpx.USE_CLIENT_DEFAULT if timeout_seconds is None
                         else httpx.Timeout(timeout_seconds)),
            )
        except httpx.HTTPError as exc:
            raise PhaseRequestFailed(self.phase, path, type(exc).__name__, str(exc)) from exc
        status = response.status_code
        if 200 <= status < 300:
            try:
                return response.json()
            except ValueError as exc:
                raise PhaseRequestFailed(self.phase, path, "unreadable_body",
                                         response.text[:_DETAIL_LIMIT]) from exc
        code, message = _error_shape(response)
        if status >= 500 or status in _RETRYABLE_4XX:
            raise PhaseRequestFailed(self.phase, path, code, message)
        raise PhaseRequestRefused(self.phase, path, status, code, message)


UNAVAILABLE_STEPS: Final[Mapping[Step, tuple[str, str]]] = {
    Step.UPLOAD: ("R5", (
        "P7 /youtube/publish needs a rendered C6 media package from P6, and its write "
        "guard must reserve under the pipeline's idempotency key")),
    Step.SUBMIT_DISTRIBUTION: ("R5", (
        "P8 needs the released audio and artwork from P6, and its write guard must "
        "reserve under the pipeline's idempotency key")),
    Step.AWAIT_PROCESSING: ("R5", (
        "no rendered upload exists to wait on until P6 and P7 are wired end to end")),
    Step.EVALUATE_OUTCOME: ("R5", (
        "P9 /analytics/evaluation/feedback needs measured views, watch time, net "
        "contribution and attribution for the published unit; no HTTP source provides "
        "them for a run yet")),
    Step.DISPATCH_REWARD: ("R5", (
        "P9 exposes no HTTP surface to dispatch or read its reward outbox; "
        "`plm analytics dispatch` runs the dispatcher")),
}
"""Steps today's phase APIs cannot carry, with the owning stage and the reason."""


def _require(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping or mapping[key] in (None, ""):
        raise ValidationFailure(f"{where}: required fact {key!r} is missing")
    return mapping[key]


_QA_PASSING: Final[frozenset[str]] = frozenset({"PASSED", "PASSED_WITH_WARNINGS"})
_QA_DISPOSITIONS: Final[frozenset[str]] = _QA_PASSING | {"REJECTED", "QUARANTINED"}


def _qa_evaluation(candidate_id: str, evaluated: Any) -> dict[str, Any]:
    """One P5 answer, checked: a disposition without the facts it implies is no answer."""
    where = f"P5 evaluation of {candidate_id}"
    disposition = str(_require(evaluated, "disposition", where))
    if disposition not in _QA_DISPOSITIONS:
        raise ValidationFailure(f"{where}: unknown disposition {disposition!r}")
    record: dict[str, Any] = {
        "candidate_id": candidate_id,
        "evaluation_id": str(_require(evaluated, "evaluation_id", where)),
        "disposition": disposition,
        "release_manifest_id": None,
        "quarantine_case_id": None,
        "reason_codes": [],
    }
    if disposition in _QA_PASSING:
        # A pass P5 issued no release manifest for is not a pass.
        record["release_manifest_id"] = str(_require(evaluated, "release_manifest_id", where))
    elif disposition == "QUARANTINED":
        record["quarantine_case_id"] = str(_require(evaluated, "quarantine_case_id", where))
        codes = _require(evaluated, "quarantine_reason_codes", where)
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


class HttpPhaseSteps:
    """:class:`~plm_platform.pipeline.activities.PhaseSteps` over the phase APIs."""

    def __init__(self, clients: Mapping[Phase, PhaseClient]) -> None:
        self._clients = dict(clients)

    def _client(self, phase: Phase) -> PhaseClient:
        client = self._clients.get(phase)
        if client is None:
            raise ValidationFailure(f"no client configured for {phase.name}")
        return client

    @staticmethod
    def _unavailable(step: Step) -> CapabilityUnavailable:
        stage, reason = UNAVAILABLE_STEPS[step]
        return CapabilityUnavailable(f"pipeline.{step.value}", owning_stage=stage, reason=reason)

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
        horizon = _require(directive, "outcome_horizon", "P2 directive")
        created = self._client(Phase.EXPERIMENTS).post(
            "/experiments/v1/experiments", idempotency_key=key, body={"directive": directive})
        experiment = _require(created, "experiment", "P3 create experiment")
        experiment_id = str(_require(experiment, "experiment_id", "P3 experiment"))
        treatments = [str(_require(t, "treatment_id", "P3 treatment"))
                      for t in experiment.get("treatments") or []]
        if not treatments:
            raise ValidationFailure(f"P3 experiment {experiment_id} has no treatments")
        return StepResult(
            outputs={"experiment_id": experiment_id, "treatment_ids": treatments,
                     "outcome_horizon": str(horizon),
                     "arm_id": str(_require(directive, "arm_id", "P2 directive")),
                     "decision_id": str(_require(directive, "decision_id", "P2 directive"))},
            provider_ids={"experiment_id": experiment_id},
        )

    # -- policy check: P3 preflight validation, then one package per treatment -------------

    async def policy_check(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._policy_check, request)

    def _policy_check(self, request: StepRequest) -> StepResult:
        key = request.idempotency_key
        research = _require(request.inputs, "research", "policy_check inputs")
        experiment_id = str(_require(research, "experiment_id", "research outputs"))
        treatments = _require(research, "treatment_ids", "research outputs")
        p3 = self._client(Phase.EXPERIMENTS)
        validated = p3.post(f"/experiments/v1/experiments/{experiment_id}/validate",
                            idempotency_key=key)
        report = _require(validated, "report", "P3 validate")
        if report.get("passed") is not True:
            raise PolicyRejection(f"P3 preflight did not pass for {experiment_id}: {report}")
        packages: list[str] = []
        for treatment_id in treatments:
            materialized = p3.post(
                f"/experiments/v1/experiments/{experiment_id}/materialize", idempotency_key=key,
                body={"treatment_id": treatment_id, "brand_id": request.brand_id})
            package = _require(materialized, "package", "P3 materialize")
            packages.append(str(_require(package, "package_id", "P3 package")))
        return StepResult(
            outputs={"experiment_id": experiment_id, "validated": True,
                     "package_ids": packages},
            provider_ids={f"package.{n}": pid for n, pid in enumerate(packages)},
        )

    # -- generate: P4 plan + execute per track spec, export each candidate to P5 -----------

    async def generate(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._generate, request)

    def _generate(self, request: StepRequest) -> StepResult:
        key = request.idempotency_key
        policy = _require(request.inputs, "policy_check", "generate inputs")
        package_ids = _require(policy, "package_ids", "policy_check outputs")
        p3, p4 = self._client(Phase.EXPERIMENTS), self._client(Phase.MUSIC)
        provider_ids: dict[str, str] = {}
        candidates: list[str] = []
        plans: list[str] = []
        for package_id in package_ids:
            package = p3.get(f"/experiments/v1/packages/{package_id}", idempotency_key=key)
            for spec in _require(package, "track_specs", f"P3 package {package_id}"):
                plan = p4.post("/music/plans", idempotency_key=key, body=spec)
                plan_id = str(_require(plan, "plan_id", "P4 plan"))
                plans.append(plan_id)
                run = p4.post(f"/music/plans/{plan_id}/execute", idempotency_key=key, body=spec)
                for candidate in run.get("candidates") or []:
                    candidate_id = str(_require(candidate, "candidate_id", "P4 candidate"))
                    p4.post(f"/music/candidates/{candidate_id}/export-to-p5",
                            idempotency_key=key)
                    candidates.append(candidate_id)
        for n, plan_id in enumerate(plans):
            provider_ids[f"plan.{n}"] = plan_id
        for n, candidate_id in enumerate(candidates):
            provider_ids[f"candidate.{n}"] = candidate_id
        if not candidates:
            # Provider calls were made and may have spent credits: not a no-effect failure.
            raise CreativeRejection(f"P4 produced no candidates for plans {plans}")
        return StepResult(
            outputs={"plan_ids": plans, "candidate_ids": candidates},
            provider_ids=provider_ids,
            cost=None,  # P4 reports provider credits, not money; the ledger books the estimate
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
        generated = _require(request.inputs, "generate", "qa inputs")
        candidate_ids = _require(generated, "candidate_ids", "generate outputs")
        if not isinstance(candidate_ids, list):
            raise ValidationFailure("generate outputs: 'candidate_ids' is not a list")
        p4, p5 = self._client(Phase.MUSIC), self._client(Phase.QA)
        evaluations: list[dict[str, Any]] = []
        for candidate_id in candidate_ids:
            exported = p4.post(f"/music/candidates/{candidate_id}/export-to-p5",
                               idempotency_key=key)
            event = _require(exported, "event", f"P4 export of {candidate_id}")
            evaluated = p5.post("/qa/evaluations", idempotency_key=key,
                                body={"candidate_event": event})
            evaluations.append(_qa_evaluation(str(candidate_id), evaluated))
        return _qa_result(evaluations)

    # -- steps today's APIs cannot carry ---------------------------------------------------

    # -- assemble: P6 assembles and renders each P3 package's cleared recordings (R4) -----

    ASSEMBLE_TIMEOUT_SECONDS: Final[float] = 570.0
    """P6 renders inside the request; this stays under the step's start-to-close."""

    async def assemble(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._assemble, request)

    @staticmethod
    def _playlist_facts(package: Any, package_id: str) -> dict[str, Any]:
        """P3's playlist, visual and title facts for P6, never defaulted.

        ``required_duration_seconds`` is the playlist spec's total duration;
        ``visual_profile`` the visual spec's visual world; ``playlist_concept`` the title
        spec's formatting pattern filled with the first track spec's genre, mood and use
        case (P3's declared title rule over P3's own facts).
        """
        where = f"P3 package {package_id}"
        playlist = _require(package, "playlist_spec", where)
        visual = _require(package, "visual_spec", where)
        title = _require(package, "title_spec", where)
        specs = _require(package, "track_specs", where)
        if not isinstance(specs, list) or not specs:
            raise ValidationFailure(f"{where}: no track specs")
        first = specs[0]
        preferences = _require(first, "bounded_preferences", f"{where} track spec")
        constraints = _require(first, "hard_constraints", f"{where} track spec")
        moods = _require(preferences, "mood", f"{where} preferences")
        uses = _require(preferences, "use_case", f"{where} preferences")
        if not isinstance(moods, list) or not moods or not isinstance(uses, list) or not uses:
            raise ValidationFailure(f"{where}: mood and use case are needed for the concept")
        pattern = str(_require(title, "formatting_pattern", f"{where} title spec"))
        try:
            concept = pattern.format_map({
                "mood": moods[0], "use_case": uses[0],
                "genre": _require(constraints, "genre", f"{where} constraints")})
        except (KeyError, IndexError, ValueError) as exc:
            raise ValidationFailure(
                f"{where}: title pattern {pattern!r} needs a fact P3 does not give: {exc}"
            ) from exc
        minutes = _require(playlist, "total_duration_minutes", f"{where} playlist spec")
        return {
            "brand_id": str(_require(package, "brand_id", where)),
            "required_duration_seconds": float(minutes) * 60.0,
            "visual_profile": str(_require(visual, "visual_world", f"{where} visual spec")),
            "playlist_concept": concept,
            "track_spec_ids": {str(_require(s, "track_spec_id", where)) for s in specs},
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
        """One media package per P3 package that has cleared recordings.

        Inputs: ``policy_check.package_ids`` (P3 packages) and ``generate.candidate_ids``.
        Each package's cleared candidates (by track spec) go to
        ``POST /media/packages/assemble`` by id with the package's playlist facts; P6
        fetches the manifests from P5 itself. A package P6 assembled but whose render
        post-render QA blocked fails the step with P6's reason codes; a P6 refusal fails
        it with P6's code. No package with a cleared recording fails it too.
        """
        key = request.idempotency_key
        policy = _require(request.inputs, "policy_check", "assemble inputs")
        package_ids = _require(policy, "package_ids", "policy_check outputs")
        generated = _require(request.inputs, "generate", "assemble inputs")
        candidate_ids = _require(generated, "candidate_ids", "generate outputs")
        if not isinstance(package_ids, list) or not isinstance(candidate_ids, list):
            raise ValidationFailure("assemble inputs: package and candidate ids must be lists")
        cleared = self._cleared_track_specs(candidate_ids, key)
        p3, p6 = self._client(Phase.EXPERIMENTS), self._client(Phase.MEDIA)
        packages: list[dict[str, Any]] = []
        for raw_package_id in package_ids:
            package_id = str(raw_package_id)
            facts = self._playlist_facts(
                p3.get(f"/experiments/v1/packages/{package_id}", idempotency_key=key),
                package_id)
            members = [cid for cid, spec in cleared.items() if spec in facts["track_spec_ids"]]
            if not members:
                continue
            assembled = p6.post(
                "/media/packages/assemble", idempotency_key=key,
                timeout_seconds=self.ASSEMBLE_TIMEOUT_SECONDS,
                body={"candidate_ids": members, "brand_id": facts["brand_id"],
                      "required_duration_seconds": facts["required_duration_seconds"],
                      "visual_profile": facts["visual_profile"],
                      "playlist_concept": facts["playlist_concept"]})
            media_package_id = str(_require(assembled, "media_package_id", "P6 package"))
            package_status = str(_require(assembled, "status", "P6 package"))
            if package_status != "ready_for_publish":
                codes = assembled.get("blocking_reason_codes") or []
                raise ValidationFailure(
                    f"P6 package {media_package_id} is {package_status}: post-render QA "
                    f"blocked it ({', '.join(map(str, codes)) or 'no reason codes'})")
            packages.append({"media_package_id": media_package_id, "p3_package_id": package_id,
                             "candidate_ids": members,
                             "video_checksum": _require(assembled, "video_checksum",
                                                        "P6 package")})
        if not packages:
            raise ValidationFailure(
                f"assemble: none of the P3 packages {package_ids} has a recording P5 cleared")
        return StepResult(
            outputs={"media_package_ids": [p["media_package_id"] for p in packages],
                     "packages": packages},
            provider_ids={f"media_package.{n}": p["media_package_id"]
                          for n, p in enumerate(packages)},
        )

    async def upload(self, request: StepRequest) -> StepResult:
        raise self._unavailable(Step.UPLOAD)

    async def submit_distribution(self, request: StepRequest) -> StepResult:
        raise self._unavailable(Step.SUBMIT_DISTRIBUTION)

    async def await_processing(self, request: StepRequest) -> StepResult:
        raise self._unavailable(Step.AWAIT_PROCESSING)

    async def evaluate_outcome(self, request: StepRequest) -> StepResult:
        raise self._unavailable(Step.EVALUATE_OUTCOME)

    async def dispatch_reward(self, request: StepRequest) -> StepResult:
        raise self._unavailable(Step.DISPATCH_REWARD)

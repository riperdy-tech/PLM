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
(``policy_check``), and P4 plan + execute + export to P5 (``generate``). Every other step
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

    def post(self, path: str, *, idempotency_key: str, body: Any = None) -> Any:
        return self._request("POST", path, idempotency_key=idempotency_key, body=body)

    def _request(self, method: str, path: str, *, idempotency_key: str,
                 params: Mapping[str, str] | None = None, body: Any = None) -> Any:
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
    Step.QA: ("R4", (
        "P5 evaluates audio it reads by URI from the artifact store; P4 does not hand "
        "audio bytes over by URI until R4")),
    Step.ASSEMBLE: ("R4", "P6 needs P5 gate decisions and real FFmpeg rendering (R4)"),
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

    # -- steps today's APIs cannot carry ---------------------------------------------------

    async def qa(self, request: StepRequest) -> StepResult:
        raise self._unavailable(Step.QA)

    async def assemble(self, request: StepRequest) -> StepResult:
        raise self._unavailable(Step.ASSEMBLE)

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

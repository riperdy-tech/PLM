"""HTTP phase steps against an in-process ``httpx.MockTransport``.

Proves the request shapes (service token and ``Idempotency-Key`` on every call, one key
per write inside a step, the paths and bodies of P2/P3/P4) and the answer
classification, including a write guard's 409 becoming :class:`ExternalWriteRefused`
with the hold its reason calls for. It proves nothing about the real phase services;
their own tests and Gate R5 do. The R5 steps are in ``test_pipeline_http_r5.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from plm_platform.pipeline.http import (
    ExternalWriteRefused,
    HttpPhaseSteps,
    PhaseClient,
    PhaseRequestFailed,
    PhaseRequestRefused,
)
from plm_platform.pipeline.phase_client import request_key
from plm_platform.pipeline.states import Step
from plm_platform.pipeline.types import StepRequest
from plm_platform.service_urls import service_url, service_urls
from plm_platform.temporal import CreativeRejection, Phase, PolicyRejection, ValidationFailure

TOKEN = "unit-token"
KEY = "pipeline.dir-1:research"
Handler = Callable[[httpx.Request], httpx.Response]


def _client(phase: Phase, handler: Handler, token: str | None = TOKEN) -> PhaseClient:
    transport = httpx.MockTransport(handler)
    return PhaseClient(phase, httpx.Client(base_url="http://phase.test", transport=transport),
                       token=token)


def _request(step: Step, inputs: dict[str, Any] | None = None) -> StepRequest:
    return StepRequest(workflow_id="pipeline.dir-1", directive_id="dir-1", brand_id="brand",
                       step=step.value, idempotency_key=f"pipeline.dir-1:{step.value}",
                       requested_at=datetime(2026, 10, 14, tzinfo=UTC), inputs=inputs or {})


def test_every_request_carries_the_token_and_the_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = _client(Phase.AR, handler)
    assert client.post("/x", idempotency_key=KEY, body={"a": 1}) == {"ok": True}
    assert client.get("/y", idempotency_key=KEY, params={"q": "1"}) == {"ok": True}
    for request in seen:
        assert request.headers["X-PLM-Service-Token"] == TOKEN
        assert request.headers["Idempotency-Key"] == KEY


@pytest.mark.parametrize(("status", "error"), [
    (422, PhaseRequestRefused), (404, PhaseRequestRefused), (409, PhaseRequestRefused),
    (500, PhaseRequestFailed), (503, PhaseRequestFailed), (401, PhaseRequestFailed),
    (429, PhaseRequestFailed),
])
def test_answers_are_classified_refused_or_retryable(status: int, error: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": {"code": "some_code", "message": "m",
                                                       "field_paths": []}})

    with pytest.raises(error) as raised:
        _client(Phase.EXPERIMENTS, handler).post("/x", idempotency_key=KEY)
    assert getattr(raised.value, "code", None) == "some_code"


def test_a_connection_failure_is_retryable_and_no_token_is_refused() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(PhaseRequestFailed):
        _client(Phase.MUSIC, broken).get("/x", idempotency_key=KEY)
    with pytest.raises(PhaseRequestRefused) as raised:
        _client(Phase.MUSIC, broken, token=None).get("/x", idempotency_key=KEY)
    assert raised.value.code == "blocked_pending_credentials"


@pytest.mark.parametrize(("reason", "hold"), [
    ("budget_exhausted", "BUDGET_RESERVATION_REFUSED"),
    ("capability_budget_exhausted", "BUDGET_RESERVATION_REFUSED"),
    ("budget_ledger_unavailable", "BUDGET_RESERVATION_REFUSED"),
    ("no_authorization", "EXTERNAL_WRITE_NOT_AUTHORIZED"),
    ("policy_dry_run", "EXTERNAL_WRITE_NOT_AUTHORIZED"),
    ("something_new", "EXTERNAL_WRITE_REFUSED"),
])
def test_a_write_guard_refusal_names_the_hold_its_reason_calls_for(reason: str,
                                                                    hold: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": {
            "code": "external_write_deny", "field_paths": [],
            "message": f"distribution.submit not performed: {reason}"}})

    with pytest.raises(ExternalWriteRefused) as raised:
        _client(Phase.DISTRIBUTION, handler).post("/distribution/releases",
                                                  idempotency_key=KEY)
    refusal = raised.value
    assert refusal.no_effect and refusal.capability == "distribution.submit"
    assert (refusal.guard_decision, refusal.guard_reason) == ("deny", reason)
    assert refusal.details["hold_reason_code"] == hold
    assert ("ledger_reason" in refusal.details) is (hold == "BUDGET_RESERVATION_REFUSED")


def test_a_409_that_is_not_a_guard_refusal_stays_an_ordinary_refusal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": {"code": "channel_not_configured",
                                                    "field_paths": [], "message": "m"}})

    with pytest.raises(PhaseRequestRefused) as raised:
        _client(Phase.YOUTUBE, handler).post("/youtube/publish", idempotency_key=KEY)
    assert not isinstance(raised.value, ExternalWriteRefused)


def test_each_write_inside_a_step_has_its_own_key() -> None:
    assert request_key(KEY, "plan", "ts-1") == f"{KEY}/plan/ts-1"
    assert request_key(KEY, "dispatch") == f"{KEY}/dispatch"
    assert request_key(KEY, "plan", "ts-1") != request_key(KEY, "plan", "ts-2")
    with pytest.raises(ValueError):
        request_key(KEY, " ")


DIRECTIVE = {"directive_id": "dir-1", "decision_id": "dec-1", "arm_id": "arm-1",
             "outcome_horizon": "28d", "schema_version": "2.0"}


def test_research_reads_the_queued_directive_and_designs_the_experiment() -> None:
    posted: dict[str, Any] = {}

    def p2(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ar/directives"
        assert request.url.params["brand_id"] == "brand"
        return httpx.Response(200, json=[{"directive_id": "other"}, DIRECTIVE])

    def p3(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/experiments/v1/experiments"
        posted.update(json.loads(request.content))
        return httpx.Response(200, json={"experiment": {
            "experiment_id": "exp-1", "treatments": [{"treatment_id": "t-1"}]}})

    steps = HttpPhaseSteps({Phase.AR: _client(Phase.AR, p2),
                            Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3)})
    result = asyncio.run(steps.research(_request(Step.RESEARCH)))
    assert posted == {"directive": DIRECTIVE}
    assert result.outputs["experiment_id"] == "exp-1"
    assert result.outputs["outcome_horizon"] == "28d"  # from the directive, never a default
    assert result.outputs["treatment_ids"] == ["t-1"]


def test_research_refuses_a_directive_p2_does_not_queue() -> None:
    steps = HttpPhaseSteps({
        Phase.AR: _client(Phase.AR, lambda r: httpx.Response(200, json=[])),
        Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, lambda r: httpx.Response(500)),
    })
    with pytest.raises(PolicyRejection):
        asyncio.run(steps.research(_request(Step.RESEARCH)))


def test_policy_check_requires_a_passing_preflight_then_materializes_each_treatment() -> None:
    calls: list[str] = []

    def p3(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/validate"):
            return httpx.Response(200, json={"report": {"passed": True}, "status": "validated"})
        body = json.loads(request.content)
        return httpx.Response(200, json={"package": {"package_id": f"pkg-{body['treatment_id']}"}})

    steps = HttpPhaseSteps({Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3)})
    inputs = {"research": {"experiment_id": "exp-1", "treatment_ids": ["t-1", "t-2"]}}
    result = asyncio.run(steps.policy_check(_request(Step.POLICY_CHECK, inputs)))
    assert result.outputs["package_ids"] == ["pkg-t-1", "pkg-t-2"]
    assert calls == ["/experiments/v1/experiments/exp-1/validate",
                     "/experiments/v1/experiments/exp-1/materialize",
                     "/experiments/v1/experiments/exp-1/materialize"]

    failing = HttpPhaseSteps({Phase.EXPERIMENTS: _client(
        Phase.EXPERIMENTS, lambda r: httpx.Response(200, json={"report": {"passed": False}}))})
    with pytest.raises(PolicyRejection):
        asyncio.run(failing.policy_check(_request(Step.POLICY_CHECK, inputs)))


def test_generate_plans_executes_and_exports_every_candidate() -> None:
    spec = {"track_spec_id": "ts-1"}

    def p3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"package_id": "pkg-1", "track_specs": [spec]})

    p4_paths: list[str] = []
    p4_keys: list[str] = []
    receipt = {"capability": "music.generate", "idempotency_key": "k", "decision": "simulate",
               "reason": "mock_provider"}

    def p4(request: httpx.Request) -> httpx.Response:
        p4_paths.append(request.url.path)
        p4_keys.append(request.headers["Idempotency-Key"])
        if request.url.path == "/music/plans":
            return httpx.Response(200, json={"plan_id": "plan-1"})
        if request.url.path.endswith("/execute"):
            return httpx.Response(200, json={"status": "completed",
                                             "candidates": [{"candidate_id": "c-1"}],
                                             "external_writes": [receipt]})
        return httpx.Response(200, json={"candidate": {}, "event": {}})

    steps = HttpPhaseSteps({Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3),
                            Phase.MUSIC: _client(Phase.MUSIC, p4)})
    inputs = {"policy_check": {"package_ids": ["pkg-1"]}}
    result = asyncio.run(steps.generate(_request(Step.GENERATE, inputs)))
    assert p4_paths == ["/music/plans", "/music/plans/plan-1/execute",
                        "/music/candidates/c-1/export-to-p5"]
    step_key = "pipeline.dir-1:generate"
    assert p4_keys == [f"{step_key}/plan/ts-1", f"{step_key}/execute/plan-1",
                       f"{step_key}/export/c-1"]
    assert result.outputs["plan_ids"] == ["plan-1"]
    assert result.outputs["candidate_ids"] == ["c-1"]
    assert result.outputs["candidates_by_package"] == {"pkg-1": ["c-1"]}
    assert result.outputs["external_writes"] == [{**receipt, "p3_package_id": "pkg-1"}]
    assert result.cost is None  # P4 reports credits, never money: no invented amount

    # A top-up generates only the specs it is given; P4 without a guard receipt is
    # recorded as an unreported write (its cost then stays unknown).
    p4_paths.clear()
    bare = HttpPhaseSteps({
        Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, lambda r: httpx.Response(
            200, json={"track_specs": [spec, {"track_spec_id": "ts-2"}]})),
        Phase.MUSIC: _client(Phase.MUSIC, lambda r: httpx.Response(
            200, json={"plan_id": "plan-2", "status": "completed",
                       "candidates": [{"candidate_id": "c-2"}]})),
    })
    topped = asyncio.run(bare.generate(_request(Step.GENERATE, {
        **inputs, "track_spec_ids": ["ts-2"]})))
    assert topped.outputs["candidate_ids"] == ["c-2"]
    (unreported,) = topped.outputs["external_writes"]
    assert unreported["decision"] == "unreported" and unreported["p3_package_id"] == "pkg-1"

    empty = HttpPhaseSteps({
        Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3),
        Phase.MUSIC: _client(Phase.MUSIC, lambda r: httpx.Response(
            200, json={"plan_id": "plan-1", "status": "completed", "candidates": []})),
    })
    with pytest.raises(CreativeRejection):
        asyncio.run(empty.generate(_request(Step.GENERATE, inputs)))


@pytest.mark.parametrize(
    ("bad_status", "error_type"),
    [
        ("failed", CreativeRejection),
        ("running", ValidationFailure),
        (None, ValidationFailure),
    ],
)
def test_generation_does_not_export_any_candidate_if_later_plan_is_not_complete(
    bad_status: str | None, error_type: type[Exception],
) -> None:
    """A failed P4 plan cannot be silently passed to P5, even after earlier success."""
    p4_paths: list[str] = []

    def p3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "track_specs": [{"track_spec_id": "ts-1"}, {"track_spec_id": "ts-2"}]
        })

    def p4(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        p4_paths.append(path)
        if path == "/music/plans":
            spec = json.loads(request.content)
            return httpx.Response(200, json={"plan_id": spec["track_spec_id"]})
        if path.endswith("/execute"):
            status = "completed" if "/ts-1/" in path else bad_status
            response: dict[str, Any] = {
                "candidates": [{"candidate_id": f"candidate-{path.split('/')[3]}"}],
                "external_writes": [{"decision": "simulate", "reason": "mock_provider"}],
            }
            if status is not None:
                response["status"] = status
            if status == "failed":
                response["errors"] = ["candidate_target_not_met: 1/2"]
            return httpx.Response(200, json=response)
        raise AssertionError(f"P5 export must not happen: {path}")

    steps = HttpPhaseSteps({
        Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3),
        Phase.MUSIC: _client(Phase.MUSIC, p4),
    })
    with pytest.raises(error_type):
        asyncio.run(steps.generate(_request(Step.GENERATE, {
            "policy_check": {"package_ids": ["pkg-1"]},
        })))
    assert p4_paths == [
        "/music/plans", "/music/plans/ts-1/execute",
        "/music/plans", "/music/plans/ts-2/execute",
    ]


def test_generation_exports_only_after_all_plans_confirm_completion() -> None:
    """Do not expose half a package to P5 before confirming its last P4 plan."""
    calls: list[str] = []

    def p3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "track_specs": [{"track_spec_id": "a"}, {"track_spec_id": "b"}],
        })

    def p4(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/music/plans":
            return httpx.Response(200, json={
                "plan_id": json.loads(request.content)["track_spec_id"],
            })
        if request.url.path.endswith("/execute"):
            return httpx.Response(200, json={
                "status": "completed",
                "candidates": [{"candidate_id": request.url.path.split("/")[3]}],
                "external_writes": [],
            })
        return httpx.Response(200, json={"event": {}})

    steps = HttpPhaseSteps({
        Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3),
        Phase.MUSIC: _client(Phase.MUSIC, p4),
    })
    result = asyncio.run(steps.generate(_request(Step.GENERATE, {
        "policy_check": {"package_ids": ["pkg-1"]},
    })))
    assert result.outputs["candidate_ids"] == ["a", "b"]
    assert calls == [
        "/music/plans", "/music/plans/a/execute",
        "/music/plans", "/music/plans/b/execute",
        "/music/candidates/a/export-to-p5",
        "/music/candidates/b/export-to-p5",
    ]


@pytest.mark.parametrize(
    ("second_candidates", "error_type"),
    [
        ([], CreativeRejection),
        ("not-a-list", ValidationFailure),
        ([{"candidate_id": 123}], ValidationFailure),
        ([{"candidate_id": "  "}], ValidationFailure),
        ([{"candidate_id": None}], ValidationFailure),
        ([{"wrong_key": "x"}], ValidationFailure),
        ([{"candidate_id": "c-1"}], ValidationFailure),
    ],
)
def test_generate_refuses_malformed_or_empty_later_plan_without_any_c4_export(
    second_candidates: Any, error_type: type[Exception],
) -> None:
    """A completed status must not mask absent/invalid/duplicate candidate identities."""
    calls: list[str] = []

    def p3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "track_specs": [{"track_spec_id": "ts-1"}, {"track_spec_id": "ts-2"}],
        })

    def p4(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path == "/music/plans":
            return httpx.Response(200, json={
                "plan_id": json.loads(request.content)["track_spec_id"],
            })
        if path.endswith("/execute"):
            return httpx.Response(200, json={
                "status": "completed",
                "candidates": (
                    [{"candidate_id": "c-1"}] if "/ts-1/" in path else second_candidates
                ),
            })
        raise AssertionError(f"unexpected C4 export after invalid plan: {path}")

    steps = HttpPhaseSteps({
        Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3),
        Phase.MUSIC: _client(Phase.MUSIC, p4),
    })
    with pytest.raises(error_type):
        asyncio.run(steps.generate(_request(Step.GENERATE, {
            "policy_check": {"package_ids": ["pkg-1"]},
        })))
    assert calls == [
        "/music/plans", "/music/plans/ts-1/execute",
        "/music/plans", "/music/plans/ts-2/execute",
    ]


@pytest.mark.parametrize(
    ("reported", "retained", "reason"),
    [
        (None, 0, "p4_reported_no_guard_receipt"),
        ([], 0, "p4_reported_no_guard_receipt"),
        ("bad-shape", 0, "p4_reported_no_guard_receipt"),
        ([None], 0, "p4_reported_invalid_guard_receipt"),
        ([{}], 0, "p4_reported_invalid_guard_receipt"),
        ([{"decision": "simulate"}], 0, "p4_reported_invalid_guard_receipt"),
        ([{"capability": "music.generate", "idempotency_key": "att-1",
           "decision": "simulate"}, None], 1, "p4_reported_invalid_guard_receipt"),
    ],
)
def test_generate_preserves_unknown_cost_for_missing_or_malformed_guard_receipts(
    reported: Any, retained: int, reason: str,
) -> None:
    """Incomplete P4 receipts must not erase potential paid spend from the workflow."""
    def p3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"track_specs": [{"track_spec_id": "ts-1"}]})

    def p4(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/music/plans":
            return httpx.Response(200, json={"plan_id": "plan-1"})
        if path.endswith("/execute"):
            return httpx.Response(200, json={
                "status": "completed",
                "candidates": [{"candidate_id": "candidate-1"}],
                "external_writes": reported,
            })
        return httpx.Response(200, json={"event": {}})

    steps = HttpPhaseSteps({
        Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3),
        Phase.MUSIC: _client(Phase.MUSIC, p4),
    })
    result = asyncio.run(steps.generate(_request(Step.GENERATE, {
        "policy_check": {"package_ids": ["pkg-1"]},
    })))
    receipts = result.outputs["external_writes"]
    assert len(receipts) == retained + 1
    if retained:
        assert receipts[0]["idempotency_key"] == "att-1"
        assert receipts[0]["p3_package_id"] == "pkg-1"
    unknown = receipts[-1]
    assert unknown == {
        "capability": "music.generate",
        "idempotency_key": "pipeline.dir-1:generate/execute/plan-1",
        "decision": "unreported",
        "reason": reason,
        "p3_package_id": "pkg-1",
    }


@pytest.mark.parametrize(
    ("attempts_count", "duplicate", "expected_reason"),
    [
        (1, False, None),
        (2, False, "p4_attempt_count_receipt_mismatch"),
        (2, True, "p4_attempt_count_receipt_mismatch"),
        ("2", False, "p4_attempt_count_receipt_mismatch"),
        (True, False, "p4_attempt_count_receipt_mismatch"),
    ],
)
def test_generate_reconciles_reported_attempt_count_with_unique_guard_receipts(
    attempts_count: Any, duplicate: bool, expected_reason: str | None,
) -> None:
    """The reported number of calls needs at least that many distinct receipts."""
    valid = {"capability": "music.generate", "idempotency_key": "paid-1",
             "decision": "execute"}

    def p3(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"track_specs": [{"track_spec_id": "ts-1"}]})

    def p4(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/music/plans":
            return httpx.Response(200, json={"plan_id": "plan-1"})
        if path.endswith("/execute"):
            return httpx.Response(200, json={
                "status": "completed",
                "candidates": [{"candidate_id": "candidate-1"}],
                "attempts_count": attempts_count,
                "external_writes": [valid, valid] if duplicate else [valid],
            })
        return httpx.Response(200, json={"event": {}})

    steps = HttpPhaseSteps({
        Phase.EXPERIMENTS: _client(Phase.EXPERIMENTS, p3),
        Phase.MUSIC: _client(Phase.MUSIC, p4),
    })
    result = asyncio.run(steps.generate(_request(Step.GENERATE, {
        "policy_check": {"package_ids": ["pkg-1"]},
    })))
    receipts = result.outputs["external_writes"]
    assert receipts[0] == {**valid, "p3_package_id": "pkg-1"}
    assert len(receipts) == (2 if expected_reason else 1)
    if expected_reason:
        assert receipts[-1]["decision"] == "unreported"
        assert receipts[-1]["reason"] == expected_reason


def test_service_urls_default_to_compose_names_or_loopback_and_accept_overrides(
    tmp_path: Any,
) -> None:
    absent = tmp_path / "none.env"
    assert service_url(Phase.AR, env={}, dotenv_path=absent) == "http://api-ar:8082"
    assert service_url(Phase.AR, env={}, dotenv_path=absent,
                       default_host="127.0.0.1") == "http://127.0.0.1:8082"
    urls = service_urls(env={"PLM_SERVICE_URL__MUSIC": "http://music.internal:9/"},
                        dotenv_path=absent)
    assert urls[Phase.MUSIC] == "http://music.internal:9"
    assert len(urls) == 9
    with pytest.raises(Exception, match="PLM_SERVICE_URL__QA"):
        service_urls(env={"PLM_SERVICE_URL__QA": "api-qa:8005"}, dotenv_path=absent)

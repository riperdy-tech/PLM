"""The pipeline ``qa`` step over HTTP (R4), against an in-process ``httpx.MockTransport``.

Proves the request shapes (P4 export per candidate, then ``POST /qa/evaluations`` with
the C4 event and nothing else, no audio path), how P5's answers become the run's
disposition and holds, and that a missing fact or a refusal is never a pass. It proves
nothing about the real services; ``tests/integration/test_r4_artifact_flow.py`` runs
the step against the compose stack.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import plm_platform.pipeline.http as http_steps
import pytest
from plm_platform.pipeline.http import (
    HttpPhaseSteps,
    PhaseRequestFailed,
    PhaseRequestRefused,
)
from plm_platform.pipeline.states import Step
from plm_platform.temporal import Phase, ValidationFailure

from tests.unit.test_pipeline_http import _client, _request

EVENT = {"candidate_id": "c-1", "artifact_uri": "plm://b/music/c-1/master.wav/" + "a" * 64}


def _p4(seen: list[str]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        candidate = request.url.path.split("/")[3]
        return httpx.Response(200, json={"candidate": {"candidate_id": candidate},
                                         "event": {**EVENT, "candidate_id": candidate}})
    return handler


def _p5(answers: dict[str, httpx.Response], bodies: list[dict[str, Any]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return answers[body["candidate_event"]["candidate_id"]]
    return handler


def _passed(candidate: str, disposition: str = "PASSED") -> httpx.Response:
    return httpx.Response(201, json={"evaluation_id": f"eval-{candidate}",
                                     "disposition": disposition,
                                     "release_manifest_id": f"rel-{candidate}",
                                     "quarantine_case_id": None, "quarantine_reason_codes": []})


def _quarantined(candidate: str, codes: list[str]) -> httpx.Response:
    return httpx.Response(201, json={"evaluation_id": f"eval-{candidate}",
                                     "disposition": "QUARANTINED",
                                     "release_manifest_id": None,
                                     "quarantine_case_id": f"quar-{candidate}",
                                     "quarantine_reason_codes": codes})


def _rejected(candidate: str) -> httpx.Response:
    return httpx.Response(201, json={"evaluation_id": f"eval-{candidate}",
                                     "disposition": "REJECTED", "release_manifest_id": None,
                                     "quarantine_case_id": None, "quarantine_reason_codes": []})


def _run(answers: dict[str, httpx.Response], candidates: list[str] | None = None,
         ) -> tuple[Any, list[str], list[dict[str, Any]]]:
    seen: list[str] = []
    bodies: list[dict[str, Any]] = []
    steps = HttpPhaseSteps({Phase.MUSIC: _client(Phase.MUSIC, _p4(seen)),
                            Phase.QA: _client(Phase.QA, _p5(answers, bodies))})
    inputs = {"generate": {"candidate_ids": candidates or list(answers)}}
    result = asyncio.run(steps.qa(_request(Step.QA, inputs)))
    return result, seen, bodies


def test_no_step_is_unavailable_any_more() -> None:
    """Proves: the R4/R5 CapabilityUnavailable placeholders are gone; every step has an
    HTTP implementation."""
    assert not hasattr(http_steps, "UNAVAILABLE_STEPS")
    assert all(callable(getattr(HttpPhaseSteps, step.value, None)) for step in Step)


def test_qa_exports_each_candidate_and_p5_evaluates_the_event_alone() -> None:
    """Proves: P4 export supplies each C4 event, P5 receives exactly that event and no
    path, and a pass carries its release manifest forward."""
    result, seen, bodies = _run({"c-1": _passed("c-1"), "c-2": _passed("c-2",
                                                                       "PASSED_WITH_WARNINGS")})
    assert seen == ["/music/candidates/c-1/export-to-p5", "/music/candidates/c-2/export-to-p5"]
    assert [set(b) for b in bodies] == [{"candidate_event"}, {"candidate_event"}]
    assert bodies[0]["candidate_event"]["artifact_uri"] == EVENT["artifact_uri"]
    assert result.outputs["disposition"] == "PASSED"
    assert result.outputs["holds"] == []
    assert result.outputs["passed_candidate_ids"] == ["c-1", "c-2"]
    assert result.outputs["release_manifest_ids"] == ["rel-c-1", "rel-c-2"]
    assert result.provider_ids == {"evaluation.0": "eval-c-1", "evaluation.1": "eval-c-2"}


def test_a_quarantine_returns_one_hold_per_reason_code() -> None:
    """Proves: when no candidate passed and one is quarantined, the run is QUARANTINED
    with the case's reason codes as holds (the workflow raises them)."""
    result, _, _ = _run({"c-1": _quarantined("c-1", ["UNKNOWN_COMMERCIAL_LICENSE",
                                                     "INCOMPLETE_R6_PROVENANCE"]),
                         "c-2": _rejected("c-2")})
    assert result.outputs["disposition"] == "QUARANTINED"
    assert [h["reason_code"] for h in result.outputs["holds"]] == [
        "UNKNOWN_COMMERCIAL_LICENSE", "INCOMPLETE_R6_PROVENANCE"]
    assert result.outputs["holds"][0]["details"] == {
        "candidate_id": "c-1", "evaluation_id": "eval-c-1", "quarantine_case_id": "quar-c-1"}
    assert result.outputs["passed_candidate_ids"] == []


def test_one_pass_carries_the_run_and_all_rejected_rejects_it() -> None:
    """Proves: a passed candidate carries the run forward past a quarantined sibling (whose
    case stays open in P5), and a run whose candidates were all rejected is REJECTED."""
    mixed, _, _ = _run({"c-1": _quarantined("c-1", ["UNKNOWN_COMMERCIAL_LICENSE"]),
                        "c-2": _passed("c-2")})
    assert mixed.outputs["disposition"] == "PASSED"
    assert mixed.outputs["passed_candidate_ids"] == ["c-2"]
    rejected, _, _ = _run({"c-1": _rejected("c-1")})
    assert rejected.outputs["disposition"] == "REJECTED"
    assert rejected.outputs["holds"] == []


@pytest.mark.parametrize("answer", [
    {"evaluation_id": "e", "disposition": None},
    {"evaluation_id": "e", "disposition": "MAYBE"},
    {"evaluation_id": "e", "disposition": "PASSED", "release_manifest_id": None},
    {"evaluation_id": "e", "disposition": "QUARANTINED", "quarantine_case_id": "q",
     "quarantine_reason_codes": []},
    {"evaluation_id": "e", "disposition": "QUARANTINED", "quarantine_case_id": None,
     "quarantine_reason_codes": ["X"]},
    {"disposition": "PASSED", "release_manifest_id": "r"},
])
def test_an_answer_missing_a_fact_is_never_a_pass(answer: dict[str, Any]) -> None:
    """Proves: a missing or unknown disposition, a pass without a manifest, or a quarantine
    without its case or reason codes fails the step instead of passing it."""
    with pytest.raises(ValidationFailure):
        _run({"c-1": httpx.Response(201, json=answer)})


@pytest.mark.parametrize(("status", "code", "error"), [
    (409, "artifact_integrity_failed", PhaseRequestRefused),
    (409, "artifact_not_found", PhaseRequestRefused),
    (422, "artifact_sha256_mismatch", PhaseRequestRefused),
    (503, "artifact_store_unavailable", PhaseRequestFailed),
])
def test_p5_artifact_refusals_end_or_retry_the_step(status: int, code: str,
                                                    error: type[Exception]) -> None:
    """Proves: P5's artifact refusals propagate: integrity, missing object and a
    self-contradicting event are definite (non-retryable), an unreachable store is
    retried; none of them produces a disposition."""
    answer = httpx.Response(status, json={"detail": {"code": code, "message": "m",
                                                     "field_paths": []}})
    with pytest.raises(error) as raised:
        _run({"c-1": answer})
    assert getattr(raised.value, "code", None) == code


def test_no_candidates_is_not_a_pass() -> None:
    """Proves: a qa step with no generated candidates fails instead of passing vacuously."""
    steps = HttpPhaseSteps({Phase.MUSIC: _client(Phase.MUSIC, _p4([])),
                            Phase.QA: _client(Phase.QA, _p5({}, []))})
    with pytest.raises(ValidationFailure):
        asyncio.run(steps.qa(_request(Step.QA, {"generate": {"candidate_ids": []}})))

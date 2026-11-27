"""Review 2026-11-26, pipeline core: the HTTP side of #11 and #12.

``qa_resolve`` against an ``httpx.MockTransport`` standing in for P5, and the phase
client's cancellation check inside a Temporal ``ActivityEnvironment``. Nothing here
proves the real P5 (its resolve route has its own tests).
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import httpx
import pytest
from plm_platform.pipeline.http import HttpPhaseSteps, PhaseRequestFailed, PhaseRequestRefused
from plm_platform.pipeline.states import STEP_ORIGIN_PHASE, Step
from plm_platform.temporal import Phase
from temporalio.testing import ActivityEnvironment

from tests.unit.test_pipeline_http import _client, _request

INPUTS = {"quarantine_case_ids": ["q-1", "q-2"], "actor": "rita", "role": "rights_reviewer",
          "evidence": {"rights_evidence_ref": "ref-1"}}


def _p5(answers: dict[str, httpx.Response], seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers[request.url.path.split("/")[3]]
    return handler


def test_qa_resolve_clears_every_p5_case_with_the_reviewers_evidence() -> None:
    """#11: each quarantine case of the cleared hold is resolved in P5 as an override by
    the reviewer, with the evidence the hold was cleared with and its own key."""
    seen: list[httpx.Request] = []
    ok = httpx.Response(200, json={"case_id": "x"})
    steps = HttpPhaseSteps({Phase.QA: _client(Phase.QA, _p5({"q-1": ok, "q-2": ok}, seen))})
    result = asyncio.run(steps.qa_resolve(_request(Step.QA_RESOLVE, INPUTS)))

    assert [r.url.path for r in seen] == ["/qa/quarantine/q-1/resolve",
                                          "/qa/quarantine/q-2/resolve"]
    assert json.loads(seen[0].content) == {
        "resolution": "CLEARED_BY_OVERRIDE", "auditor_id": "rita",
        "reviewer_role": "rights_reviewer", "evidence": {"rights_evidence_ref": "ref-1"},
        "notes": "cleared through the pipeline's quarantine hold"}
    assert seen[1].headers["Idempotency-Key"] == "pipeline.dir-1:qa_resolve/resolve/q-2"
    assert result.outputs["resolved_case_ids"] == ["q-1", "q-2"]
    assert STEP_ORIGIN_PHASE[Step.QA_RESOLVE] == "P5"


def test_qa_resolve_accepts_a_case_p5_already_resolved_and_refuses_anything_else() -> None:
    """A retried step (or a case the reviewer resolved in P5) is not a failure; a P5
    refusal of the override (wrong role, missing evidence) fails the step."""
    done = httpx.Response(409, json={"detail": {"code": "quarantine_already_resolved",
                                                "message": "already"}})
    ok = httpx.Response(200, json={})
    steps = HttpPhaseSteps({Phase.QA: _client(Phase.QA, _p5({"q-1": done, "q-2": ok}, []))})
    result = asyncio.run(steps.qa_resolve(_request(Step.QA_RESOLVE, INPUTS)))
    assert result.outputs == {"resolved_case_ids": ["q-2"],
                              "already_resolved_case_ids": ["q-1"]}

    refused = httpx.Response(409, json={"detail": {"code": "evidence_missing",
                                                   "message": "no"}})
    steps = HttpPhaseSteps({Phase.QA: _client(Phase.QA, _p5({"q-1": refused}, []))})
    with pytest.raises(PhaseRequestRefused, match="evidence_missing"):
        asyncio.run(steps.qa_resolve(_request(Step.QA_RESOLVE, {**INPUTS,
                                                                "quarantine_case_ids": ["q-1"]})))


def test_a_cancelled_step_sends_no_further_request() -> None:
    """#12: cancelling the activity does not stop the thread a step runs in; the phase
    client checks the cancellation before every request, so nothing more is sent."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client = _client(Phase.YOUTUBE, handler)
    env = ActivityEnvironment()
    finished = threading.Event()
    raised: list[BaseException] = []

    async def step() -> None:
        def run() -> None:
            try:
                client.post("/youtube/first", idempotency_key="k/1")
                env.cancel()  # the owner's Cancel arrives mid-step
                client.post("/youtube/publish", idempotency_key="k/2")
            except BaseException as exc:  # noqa: BLE001 - the test inspects it
                raised.append(exc)
            finally:
                finished.set()
        await asyncio.to_thread(run)

    with pytest.raises(asyncio.CancelledError):  # the activity itself ends at once
        asyncio.run(env.run(step))
    assert finished.wait(5)
    assert len(raised) == 1 and isinstance(raised[0], PhaseRequestFailed)
    assert "step_cancelled" in str(raised[0])
    assert [r.url.path for r in seen] == ["/youtube/first"]

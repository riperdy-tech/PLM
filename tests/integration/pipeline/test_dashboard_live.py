"""R5 dashboard over the compose PostgreSQL and Temporal: Production Queue, Exceptions, signal.

Proves, against the live ``ops`` schema and a real Temporal workflow (controlled mock phase
steps in an in-process worker, as in the R3 gate):

* the Production Queue view reads a HELD run from ``ops.pipeline_runs`` /
  ``pipeline_transitions`` with its hold reason and hold cases, and moves it out of the
  active list once the run completes;
* the Exceptions view lists the open hold with its hold class, reviewer role and required
  evidence, and stops listing it once it is decided;
* a reviewable hold refuses missing evidence through the dashboard (and the run stays
  HELD); the authorized reviewer's resume through the dashboard is recorded by the queue
  and **signalled to the real workflow**, which then completes;
* a hard stop refuses approve through the dashboard; the rights counsel's external
  resolution is recorded and signalled, and the run completes.

The phase steps are mocks: this is evidence for the dashboard's read and signal paths,
not for any phase.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from plm_platform.config import SERVICE_TOKEN_HEADER, Settings
from plm_platform.dashboard.app import build_live_components, create_live_app
from plm_platform.exceptions import ExceptionQueue
from plm_platform.pipeline.activities import PlatformOperations
from plm_platform.pipeline.mock import MockPhaseSteps
from plm_platform.pipeline.types import PipelineOutcome
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from tests.integration.pipeline.support import (
    REVIEWERS,
    Budget,
    new_run,
    running,
    start_run,
    status,
    wait_until,
    worker_for,
)
from tests.integration.pipeline.test_gate_r3_workflow import (
    HARD_STOP,
    QUARANTINE,
    RESULT_TIMEOUT,
    _scripts,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def dashboard(settings: Settings, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    if settings.service_token is None:
        pytest.skip("PLM_SERVICE_TOKEN is not set; the dashboard's actions need it")
    reviewers = tmp_path / "reviewers.json"
    reviewers.write_text(json.dumps(REVIEWERS), encoding="utf-8")
    live, actions = build_live_components(settings, reviewers_file=reviewers)
    app: FastAPI = create_live_app(live, actions, platform=settings)
    headers = {SERVICE_TOKEN_HEADER: settings.service_token.get_secret_value()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://dashboard", headers=headers,
                                 timeout=30.0) as client:
        yield client


async def _view(client: httpx.AsyncClient, name: str, panel: str) -> Any:
    response = await client.get(f"/api/views/{name}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_source"] == "live"
    entry = body["panels"][panel]
    assert entry["status"] == "ok", entry
    return entry["data"]


async def _active_run(client: httpx.AsyncClient, workflow_id: str) -> dict[str, Any] | None:
    data = await _view(client, "production-queue", "runs")
    return next((r for r in data["active"] if r["workflow_id"] == workflow_id), None)


async def _open_case(client: httpx.AsyncClient, case_id: str) -> dict[str, Any] | None:
    data = await _view(client, "exceptions", "open")
    return next((c for c in data["items"] if c["case_id"] == case_id), None)


async def test_dashboard_reads_a_held_run_and_its_resume_signals_the_workflow(
    client: Client, factory: sessionmaker[Session], budget: Budget,
    platform: PlatformOperations, dashboard: httpx.AsyncClient,
) -> None:
    steps = MockPhaseSteps(_scripts(qa=QUARANTINE))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the quarantine")
        (case_id,) = held.open_case_ids

        run = await _active_run(dashboard, workflow_id)
        assert run is not None
        assert (run["state"], run["held_from"], run["last_error_code"]) == (
            "HELD", "QA", "qa_quarantined")
        assert [(h["case_id"], h["reason_code"], h["status"]) for h in run["holds"]] == [
            (case_id, "UNKNOWN_COMMERCIAL_LICENSE", "OPEN")]

        item = await _open_case(dashboard, case_id)
        assert item is not None
        assert item["hold_class"] == "REVIEWABLE_QUARANTINE"
        assert item["reviewer_role"] == "rights_reviewer"
        assert item["allowed_actions"] == ["resume", "reject"]
        assert item["pipeline"]["workflow_id"] == workflow_id
        assert item["pipeline"]["run_state"] == "HELD"
        evidence = {name: f"ref-{name}" for name in item["required_evidence"]}

        refused = await dashboard.post(f"/api/exceptions/{case_id}/resume", json={
            "actor": "rita", "role": "rights_reviewer"})
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "evidence_missing"
        await asyncio.sleep(1.0)
        assert (await status(handle)).state == "HELD"  # a refusal never signals the run

        resumed = await dashboard.post(f"/api/exceptions/{case_id}/resume", json={
            "actor": "rita", "role": "rights_reviewer", "evidence": evidence,
            "notes": "dashboard integration test"})
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body["case"]["status"] == "RESOLVED"
        assert body["signal"]["status"] == "sent", body["signal"]
        assert body["signal"]["workflow_id"] == workflow_id
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert await _active_run(dashboard, workflow_id) is None
    finished = await _view(dashboard, "production-queue", "runs")
    assert any(r["workflow_id"] == workflow_id and r["completed_at"]
               for r in finished["recent_finished"])
    assert await _open_case(dashboard, case_id) is None
    with factory() as session:
        events = [(e.event_type.value, e.decision_code)
                  for e in ExceptionQueue().history(session, case_id)]
    assert events == [
        ("RAISED", None),
        ("RESUME_REFUSED", "evidence_missing"),      # the dashboard's refused attempt
        ("RESOLVED", "reviewer_authorized"),         # the dashboard's decision
        ("RESUME_AUTHORIZED", "already_resolved"),   # the workflow's own check on signal
    ]


async def test_dashboard_refuses_to_approve_a_hard_stop_and_signals_its_external_resolution(
    client: Client, factory: sessionmaker[Session], budget: Budget,
    platform: PlatformOperations, dashboard: httpx.AsyncClient,
) -> None:
    steps = MockPhaseSteps(_scripts(qa=HARD_STOP))
    directive_id, workflow_id = new_run()
    async with running(worker_for(client, steps, platform)):
        handle = await start_run(client, directive_id, workflow_id)
        held = await wait_until(handle, lambda s: s.state == "HELD", "the hard stop")
        (case_id,) = held.open_case_ids
        item = await _open_case(dashboard, case_id)
        assert item is not None and item["hold_class"] == "HARD_STOP"
        assert item["approve_available"] is False
        evidence = {name: f"ref-{name}" for name in item["required_evidence"]}

        approve = await dashboard.post(f"/api/exceptions/{case_id}/resume", json={
            "actor": "counsel", "role": "rights_counsel", "evidence": evidence})
        assert approve.status_code == 409
        assert approve.json()["detail"]["code"] == "hard_stop_not_overridable"

        resolved = await dashboard.post(f"/api/exceptions/{case_id}/external-resolution", json={
            "actor": "counsel", "role": "rights_counsel", "outcome": "CLEARED",
            "evidence": evidence, "notes": "dispute withdrawn"})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["signal"]["status"] == "sent"
        assert resolved.json()["signal"]["signal"] == "external_resolution"
        outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), RESULT_TIMEOUT)

    assert outcome.completed
    assert await _open_case(dashboard, case_id) is None

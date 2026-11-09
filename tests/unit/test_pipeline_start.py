"""Starting a run from the dashboard: one AUTONOMOUS P2 cycle, then its directive's run.

P2 is an httpx mock transport and ``start_pipeline`` is replaced, so this proves the
calls and the choice of directive, not P2's allocation or Temporal.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from plm_platform.pipeline import start as start_module
from plm_platform.pipeline.start import RunNotStarted, start_new_run

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def _p2(selected: int, queued: list[dict[str, Any]], seen: list[httpx.Request]
        ) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST" and request.url.path == "/ar/cycles":
            return httpx.Response(200, json={"cycle_id": "cyc_1", "selected_count": selected,
                                             "warning_codes": ["no_eligible_arm"]})
        if request.url.path == "/ar/cycles/cyc_1/decisions":
            return httpx.Response(200, json=[{"decision_id": "dec_1"}])
        if request.url.path == "/ar/directives":
            return httpx.Response(200, json=queued)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


class _Handle:
    def __init__(self, workflow_id: str) -> None:
        self.id = workflow_id


def test_a_start_runs_one_autonomous_cycle_and_starts_its_directive(
        monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[str, str]] = []

    async def fake_start(client: Any, *, directive_id: str, brand_id: str,
                         options: Any = None) -> _Handle:
        started.append((directive_id, brand_id))
        return _Handle(f"pipeline.{directive_id}")

    monkeypatch.setattr(start_module, "start_pipeline", fake_start)
    monkeypatch.setattr(start_module, "pipeline_workflow_id", lambda d: f"pipeline.{d}")
    seen: list[httpx.Request] = []
    queued = [{"directive_id": "dir_other", "decision_id": "dec_old", "arm_id": "a0"},
              {"directive_id": "dir_1", "decision_id": "dec_1", "arm_id": "arm_focus"}]
    run = asyncio.run(start_new_run(
        object(), ar_url="http://p2", token="t", brand_id="longform_signal",  # type: ignore[arg-type]
        policy_version="p2-v1", now=NOW, transport=_p2(1, queued, seen)))
    assert run.directive_id == "dir_1" and run.arm_id == "arm_focus"
    assert run.workflow_id == "pipeline.dir_1" and run.cycle_id == "cyc_1"
    assert started == [("dir_1", "longform_signal")]
    cycle = json.loads(seen[0].content)
    assert cycle == {"brand_id": "longform_signal", "slot_count": 1, "budget": 10.0,
                     "as_of": NOW.isoformat(), "policy_version": "p2-v1",
                     "activation_state": "AUTONOMOUS"}
    assert all(r.headers["X-PLM-Service-Token"] == "t" for r in seen)


def test_a_cycle_that_selects_nothing_starts_no_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(start_module, "start_pipeline",
                        lambda *a, **k: pytest.fail("a run was started"))
    with pytest.raises(RunNotStarted, match="nothing_selected.*no_eligible_arm"):
        asyncio.run(start_new_run(
            object(), ar_url="http://p2", token="t", brand_id="b",  # type: ignore[arg-type]
            policy_version="p2-v1", now=NOW, transport=_p2(0, [], [])))
    with pytest.raises(RunNotStarted, match="no_directive"):
        asyncio.run(start_new_run(
            object(), ar_url="http://p2", token="t", brand_id="b",  # type: ignore[arg-type]
            policy_version="p2-v1", now=NOW,
            transport=_p2(1, [{"directive_id": "d", "decision_id": "other"}], [])))



def test_an_owner_chosen_sub_style_goes_to_the_p2_cycle(
        monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start(client: Any, *, directive_id: str, brand_id: str,
                         options: Any = None) -> _Handle:
        return _Handle(f"pipeline.{directive_id}")

    monkeypatch.setattr(start_module, "start_pipeline", fake_start)
    monkeypatch.setattr(start_module, "pipeline_workflow_id", lambda d: f"pipeline.{d}")
    seen: list[httpx.Request] = []
    queued = [{"directive_id": "dir_1", "decision_id": "dec_1", "arm_id": "arm_mp"}]
    asyncio.run(start_new_run(
        object(), ar_url="http://p2", token="t", brand_id="longform_signal",  # type: ignore[arg-type]
        policy_version="p2-v1", now=NOW, subgenre="minimal_pulse",
        transport=_p2(1, queued, seen)))
    assert json.loads(seen[0].content)["subgenre"] == "minimal_pulse"
    asyncio.run(start_new_run(
        object(), ar_url="http://p2", token="t", brand_id="longform_signal",  # type: ignore[arg-type]
        policy_version="p2-v1", now=NOW, avoid_subgenres=("minimal_pulse",),
        transport=_p2(1, queued, seen)))
    body = json.loads(seen[-3].content)
    assert body["avoid_subgenres"] == ["minimal_pulse"] and "subgenre" not in body

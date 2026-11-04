"""Start a new pipeline run: one P2 allocation cycle, then the run for its directive.

Before this, every live run was started by hand (a cycle POSTed to P2, its directive
looked up in the database, then :func:`~.client.start_pipeline` from a one-off script).
:func:`start_new_run` is that sequence as one call, for the dashboard's Start button.

P2 picks the arm (its autonomous policy, ``activation_state`` AUTONOMOUS); this module
never chooses content. A cycle that selects nothing is a refusal with P2's warnings, not
a run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from temporalio.client import Client

from ..config import SERVICE_TOKEN_HEADER
from .client import pipeline_workflow_id, start_pipeline
from .types import PipelineOptions

__all__ = ["RunNotStarted", "StartedRun", "start_new_run"]


class RunNotStarted(RuntimeError):
    """P2 answered, but gave no directive to run (``code`` says why)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class StartedRun:
    workflow_id: str
    directive_id: str
    cycle_id: str
    arm_id: str


def _json(response: httpx.Response, what: str) -> Any:
    if response.status_code // 100 != 2:
        raise RunNotStarted("p2_refused", f"{what}: HTTP {response.status_code} "
                            f"{response.text[:500]}")
    return response.json()


async def start_new_run(
    temporal: Client,
    *,
    ar_url: str,
    token: str,
    brand_id: str,
    policy_version: str,
    now: datetime,
    budget: float = 10.0,
    options: PipelineOptions | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> StartedRun:
    """Run one AUTONOMOUS P2 cycle for ``brand_id`` at ``now`` and start the pipeline for
    the directive it queued. Raises :class:`RunNotStarted` when P2 selects nothing."""
    headers: Mapping[str, str] = {SERVICE_TOKEN_HEADER: token}
    async with httpx.AsyncClient(base_url=ar_url, headers=dict(headers), timeout=60.0,
                                 transport=transport) as p2:
        cycle = _json(await p2.post("/ar/cycles", json={
            "brand_id": brand_id, "slot_count": 1, "budget": budget,
            "as_of": now.isoformat(), "policy_version": policy_version,
            "activation_state": "AUTONOMOUS"}), "POST /ar/cycles")
        cycle_id = str(cycle["cycle_id"])
        if int(cycle.get("selected_count") or 0) < 1:
            raise RunNotStarted("nothing_selected", f"cycle {cycle_id} selected no arm "
                                f"(warnings: {', '.join(cycle.get('warning_codes') or []) or 'none'})")
        decisions = _json(await p2.get(f"/ar/cycles/{cycle_id}/decisions"),
                          f"GET /ar/cycles/{cycle_id}/decisions")
        decision_ids = {str(d["decision_id"]) for d in decisions}
        queued = _json(await p2.get("/ar/directives", params={"brand_id": brand_id}),
                       "GET /ar/directives")
    mine = [d for d in queued if str(d.get("decision_id")) in decision_ids]
    if not mine:
        raise RunNotStarted("no_directive", f"cycle {cycle_id} queued no actionable "
                            "directive for this brand")
    directive = mine[0]
    directive_id = str(directive["directive_id"])
    handle = await start_pipeline(temporal, directive_id=directive_id, brand_id=brand_id,
                                  options=options)
    assert handle.id == pipeline_workflow_id(directive_id)
    return StartedRun(workflow_id=handle.id, directive_id=directive_id, cycle_id=cycle_id,
                      arm_id=str(directive.get("arm_id")))

# tests/integration/market/test_temporal_jobs.py
"""Real Temporal, real worker registration: the scheduling plumbing, not the network."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from plm_platform.temporal import TaskQueue, connect_client
from temporalio import activity
from temporalio.worker import Worker

from services.market_intelligence.jobs import REGISTRATIONS
from services.market_intelligence.jobs.daily import DailyMarketWorkflow
from services.market_intelligence.jobs.schedules import JOB_NAMES, ensure_schedules

pytestmark = pytest.mark.integration


def test_the_worker_set_serves_the_market_queue_with_both_workflows() -> None:
    assert {registration.task_queue for registration in REGISTRATIONS} == {TaskQueue.MARKET}
    workflows = {workflow.__name__ for registration in REGISTRATIONS
                 for workflow in registration.workflows}
    assert {"DailyMarketWorkflow", "WeeklyMarketWorkflow", "BackfillWorkflow"} <= workflows


def test_the_seven_spec_job_names_exist() -> None:
    assert JOB_NAMES == (
        "market.seed_refresh", "market.youtube_discovery", "market.youtube_refresh",
        "market.taxonomy_classification", "market.feature_recompute",
        "market.opportunity_scoring", "market.stale_data_cleanup",
    )


def test_schedules_register_idempotently_against_a_live_temporal() -> None:
    async def scenario() -> None:
        try:
            client = await asyncio.wait_for(connect_client(), timeout=5)
        except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
            pytest.skip(f"temporal unreachable ({type(exc).__name__}); "
                        "run: docker compose up -d --wait")
        first = await ensure_schedules(client)
        second = await ensure_schedules(client)
        assert first == second == ("market.daily.workflow", "market.weekly.workflow")

    asyncio.run(scenario())


def test_the_daily_workflow_executes_end_to_end_against_the_live_server() -> None:
    """Real workflow, live Temporal, side-effect-free stand-ins for its activities.

    The plan's steps stop at schedule registration (P1.14 owns the acceptance run
    against the database and providers), so the workflow executes here on a test-owned
    queue (``ph1.market.p1test`` — nothing else polls it) with stub activities that
    touch no database and no provider. What is proven is that the real
    ``DailyMarketWorkflow`` deploys, runs durably, and sequences its six steps in order.
    """
    queue = "ph1.market.p1test"
    calls: list[str] = []

    def _stub(name: str):
        @activity.defn(name=name)
        async def stub(payload: dict) -> dict:
            calls.append(name)
            return {"stub": True}

        return stub

    stubs = [
        _stub(name) for name in (
            "ingest_seeds_activity", "discover_activity", "classify_activity",
            "recompute_activity", "score_activity", "reschedule_activity",
        )
    ]

    async def scenario() -> dict:
        try:
            client = await asyncio.wait_for(connect_client(), timeout=5)
        except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
            pytest.skip(f"temporal unreachable ({type(exc).__name__}); "
                        "run: docker compose up -d --wait")
        async with Worker(client, task_queue=queue, workflows=[DailyMarketWorkflow],
                          activities=stubs):
            return await asyncio.wait_for(
                client.execute_workflow(
                    DailyMarketWorkflow.run, {"now": "2026-09-20T06:00:00+00:00"},
                    id=f"market.daily.p1test.{uuid.uuid4().hex}", task_queue=queue,
                ),
                timeout=60,
            )

    result = asyncio.run(scenario())
    assert [step["job"] for step in result["steps"]] == [
        "market.seed_refresh", "market.youtube_discovery", "market.taxonomy_classification",
        "market.feature_recompute", "market.opportunity_scoring", "market.reschedule",
    ]
    assert calls == [
        "ingest_seeds_activity", "discover_activity", "classify_activity",
        "recompute_activity", "score_activity", "reschedule_activity",
    ]

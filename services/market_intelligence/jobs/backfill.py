# services/market_intelligence/jobs/backfill.py
"""On-demand backfill: collect explicit query ids outside the daily cadence (§26)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from plm_platform.temporal import DEFAULT_RETRY_POLICY
from temporalio import activity, workflow

__all__ = ["JOB_NAME", "BackfillWorkflow", "backfill_activity"]

JOB_NAME = "market.backfill"


@workflow.defn
class BackfillWorkflow:
    """`market.youtube_refresh` for the query ids the caller names — never automatic."""

    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            backfill_activity, payload or {},
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=DEFAULT_RETRY_POLICY,
        )


@activity.defn
async def backfill_activity(payload: dict) -> dict:
    from ..cli import run_daily_step

    return await asyncio.to_thread(run_daily_step, "market.youtube_refresh", payload=payload)

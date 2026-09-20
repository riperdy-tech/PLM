# services/market_intelligence/jobs/weekly.py
"""§26's weekly cycle: the full taxonomy recompute, rescoring, and stale-data cleanup.

The daily workflow runs the incremental half of §26's job list; this one runs the
expensive half on `ph1.market` (same queue, same registrations — one worker set).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from plm_platform.temporal import DEFAULT_RETRY_POLICY
from temporalio import activity, workflow

from .daily import classify_activity, recompute_activity, score_activity

__all__ = ["JOB_NAME", "WeeklyMarketWorkflow", "cleanup_activity"]

JOB_NAME = "market.weekly"


@workflow.defn
class WeeklyMarketWorkflow:
    """Full taxonomy recompute → rescore → stale-data inventory (§26)."""

    @workflow.run
    async def run(self, payload: dict) -> dict:
        now = payload.get("now") or workflow.now().isoformat()
        correlation_id = payload.get("correlation_id")
        summary: dict = {"started_at": now, "steps": []}
        steps = (
            ("market.taxonomy_classification", classify_activity,
             {"full": True}),
            ("market.feature_recompute", recompute_activity, {}),
            ("market.opportunity_scoring", score_activity, {}),
            ("market.stale_data_cleanup", cleanup_activity, {}),
        )
        for name, activity_fn, extra in steps:
            result = await workflow.execute_activity(
                activity_fn, {"now": now, "correlation_id": correlation_id, **extra},
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=DEFAULT_RETRY_POLICY,
            )
            summary["steps"].append({"job": name, "result": result})
        return summary


@activity.defn
async def cleanup_activity(payload: dict) -> dict:
    from ..cli import run_daily_step

    return await asyncio.to_thread(run_daily_step, "market.stale_data_cleanup", payload=payload)

# services/market_intelligence/jobs/daily.py
"""§26's daily cycle as one durable workflow on `ph1.market`.

Activities own every side effect (they are the retry/idempotency boundary); the
workflow only sequences them and passes the correlation id along (§AC-14).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from plm_platform.temporal import DEFAULT_RETRY_POLICY
from temporalio import activity, workflow

__all__ = [
    "JOB_NAME", "DailyMarketInput", "DailyMarketWorkflow", "classify_activity",
    "discover_activity", "ingest_seeds_activity", "recompute_activity", "reschedule_activity",
    "score_activity",
]

JOB_NAME = "market.daily"


@dataclass(frozen=True, slots=True)
class DailyMarketInput:
    """What one daily run carries; `None` means "now, inside the workflow"."""

    now: datetime | None = None
    correlation_id: str | None = None


@workflow.defn
class DailyMarketWorkflow:
    """seed refresh → discovery → classification → features/scores → reschedule."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = payload.get("now") or workflow.now().isoformat()
        correlation_id = payload.get("correlation_id")
        summary: dict[str, Any] = {"started_at": now, "steps": []}
        for name, activity_fn in (
            ("market.seed_refresh", ingest_seeds_activity),
            ("market.youtube_discovery", discover_activity),
            ("market.taxonomy_classification", classify_activity),
            ("market.feature_recompute", recompute_activity),
            ("market.opportunity_scoring", score_activity),
            ("market.reschedule", reschedule_activity),
        ):
            result = await workflow.execute_activity(
                activity_fn, {"now": now, "correlation_id": correlation_id},
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=DEFAULT_RETRY_POLICY,
            )
            summary["steps"].append({"job": name, "result": result})
        return summary


@activity.defn
async def ingest_seeds_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from ..cli import run_daily_step

    return await asyncio.to_thread(run_daily_step, "market.seed_refresh", payload=payload)


@activity.defn
async def discover_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from ..cli import run_daily_step

    return await asyncio.to_thread(run_daily_step, "market.youtube_discovery", payload=payload)


@activity.defn
async def classify_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from ..cli import run_daily_step

    return await asyncio.to_thread(run_daily_step, "market.taxonomy_classification",
                                   payload=payload)


@activity.defn
async def recompute_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from ..cli import run_daily_step

    return await asyncio.to_thread(run_daily_step, "market.feature_recompute", payload=payload)


@activity.defn
async def score_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from ..cli import run_daily_step

    return await asyncio.to_thread(run_daily_step, "market.opportunity_scoring", payload=payload)


@activity.defn
async def reschedule_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from ..cli import run_daily_step

    return await asyncio.to_thread(run_daily_step, "market.reschedule", payload=payload)

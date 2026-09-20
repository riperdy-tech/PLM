# services/market_intelligence/jobs/schedules.py
"""§26's job names and their Temporal schedules (ids are business keys, W0.7)."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from plm_platform.temporal import TaskQueue, business_workflow_id
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.service import RPCError

from .daily import DailyMarketWorkflow
from .weekly import WeeklyMarketWorkflow

__all__ = ["JOB_NAMES", "ensure_schedules"]

JOB_NAMES: Final[tuple[str, ...]] = (
    "market.seed_refresh", "market.youtube_discovery", "market.youtube_refresh",
    "market.taxonomy_classification", "market.feature_recompute",
    "market.opportunity_scoring", "market.stale_data_cleanup",
)
"""§26's seven job names, in the spec's order — the constants the workflows report."""

_SCHEDULES: Final[tuple[tuple[str, type, timedelta], ...]] = (
    ("market.daily.workflow", DailyMarketWorkflow, timedelta(hours=24)),
    ("market.weekly.workflow", WeeklyMarketWorkflow, timedelta(days=7)),
)


async def ensure_schedules(client: Client) -> tuple[str, ...]:
    """Register (or reuse) the schedules; ids derive from the business workflow id.

    `create_schedule` raises `ScheduleAlreadyRunningError` for an existing id (the
    server's `ALREADY_EXISTS`); that is success here, so re-running this function is
    idempotent. Any other RPC failure propagates.
    """
    for name, workflow_type, interval in _SCHEDULES:
        schedule_id = business_workflow_id("market", "schedule", name.split(".")[1])
        try:
            await client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        workflow_type.run, {}, id=schedule_id,
                        task_queue=TaskQueue.MARKET.value,
                    ),
                    spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)]),
                ),
            )
        except ScheduleAlreadyRunningError:
            continue  # idempotent: the schedule already exists
        except RPCError as exc:
            if "ALREADY_EXISTS" not in str(exc.status) and "already exists" not in str(exc).lower():
                raise
    return tuple(name for name, _, _ in _SCHEDULES)

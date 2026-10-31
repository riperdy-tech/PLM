# services/autonomous_ar/jobs/schedules.py
"""One weekly allocation schedule, idempotent by business name (§101)."""

from __future__ import annotations

import contextlib

from plm_platform.temporal import TaskQueue
from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleSpec

__all__ = ["JOB_NAMES", "ensure_schedules"]

JOB_NAMES = ("ar_weekly_allocation",)


async def ensure_schedules(client: Client) -> list[str]:
    """Create each named schedule once; a schedule that exists is left alone."""
    from .cycle import AllocationCycleWorkflow

    created: list[str] = []
    for job_name in JOB_NAMES:
        handle = client.get_schedule_handle(job_name)
        with contextlib.suppress(Exception):  # an unknown schedule name is the miss
            await handle.describe()
            continue  # already registered — re-creating would duplicate the cadence
        await client.create_schedule(
            job_name,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    AllocationCycleWorkflow.run,
                    {"brand_id": "neon_seoul", "slot_count": 10, "budget": 10.0,
                     "policy_version": "p2-v2"},
                    id=job_name, task_queue=TaskQueue.AR.value),
                spec=ScheduleSpec(cron_expressions=["0 6 * * 1"]),
            ),
        )
        created.append(job_name)
    return created

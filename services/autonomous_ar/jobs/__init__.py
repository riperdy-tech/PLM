# services/autonomous_ar/jobs/__init__.py
"""Worker registrations: `plm_platform.worker --task-queue ph2.ar --load
autonomous_ar.jobs:REGISTRATIONS` (the editable install exposes the top-level name)."""

from __future__ import annotations

from plm_platform.temporal import TaskQueue, WorkerSet

from .cycle import AllocationCycleWorkflow, run_cycle_activity

__all__ = ["REGISTRATIONS"]

REGISTRATIONS: tuple[WorkerSet, ...] = (
    WorkerSet(task_queue=TaskQueue.AR, workflows=(AllocationCycleWorkflow,),
              activities=(run_cycle_activity,)),
)

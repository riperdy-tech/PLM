# services/market_intelligence/jobs/__init__.py
"""Worker registrations: `plm_platform.worker --task-queue ph1.market --load
market_intelligence.jobs:REGISTRATIONS` (the editable install exposes the package
under its top-level name; compose wires this command for the deployed worker)."""

from __future__ import annotations

from plm_platform.temporal import TaskQueue, WorkerSet

from .backfill import BackfillWorkflow, backfill_activity
from .daily import (
    DailyMarketWorkflow,
    classify_activity,
    discover_activity,
    ingest_seeds_activity,
    recompute_activity,
    reschedule_activity,
    score_activity,
)
from .weekly import WeeklyMarketWorkflow, cleanup_activity

__all__ = ["REGISTRATIONS"]

REGISTRATIONS: tuple[WorkerSet, ...] = (
    WorkerSet(
        task_queue=TaskQueue.MARKET,
        workflows=(DailyMarketWorkflow, WeeklyMarketWorkflow, BackfillWorkflow),
        activities=(
            # every activity any registered workflow names must be registrable here
            # (Temporal fails an unknown activity at dispatch time, not at startup)
            ingest_seeds_activity, discover_activity, classify_activity, recompute_activity,
            score_activity, reschedule_activity, cleanup_activity, backfill_activity,
        ),
    ),
)

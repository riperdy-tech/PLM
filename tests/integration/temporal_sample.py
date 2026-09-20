"""Sample durable workflow and activity for the W0.7 restart-survival test.

Deliberately phase-free: :class:`DurableEcho` increments an external counter (a
file), holds for a while, then increments it again through the same activity.
The hold window is what ``test_temporal.py`` uses to kill the worker mid-run.

Both steps follow the platform conventions in :mod:`plm_platform.temporal`: the
workflow id is the business key, the activity receives a deterministic
``idempotency_key`` per step and skips an increment it has already recorded.
The correlation id arrives as an ordinary activity argument and the activity
re-installs it with :func:`plm_platform.correlation.correlation_scope`, so its
log lines carry it across the workflow boundary (W0.8).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from plm_platform.correlation import correlation_scope
from plm_platform.temporal import DEFAULT_RETRY_POLICY, TaskQueue, WorkerSet, idempotency_key
from temporalio import activity, workflow

__all__ = ["SAMPLE", "DurableEcho", "increment_counter"]

ACTIVITY_TIMEOUT = timedelta(seconds=30)

activity_logger = logging.getLogger(__name__)


@activity.defn
async def increment_counter(
    counter_path: str, marker: str, idempotency_key: str, correlation_id: str
) -> int:
    """Append ``marker`` once per ``idempotency_key``; return the recorded count.

    The correlation id is carried across the workflow boundary as an activity
    argument and re-installed here, which is the documented W0.8 convention for
    every side-effecting activity.
    """
    with correlation_scope(correlation_id):
        count = await asyncio.to_thread(
            _increment_once, Path(counter_path), marker, idempotency_key
        )
        activity_logger.info("counter incremented", extra={"marker": marker, "counter": count})
        return count


def _increment_once(counter_path: Path, marker: str, key: str) -> int:
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        counter_path.read_text(encoding="utf-8").splitlines()
        if counter_path.exists()
        else []
    )
    recorded_keys = [line.split("\t", 1)[1] for line in lines if "\t" in line]
    if key in recorded_keys:
        return len(recorded_keys)
    with counter_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{marker}\t{key}\n")
    return len(recorded_keys) + 1


@workflow.defn
class DurableEcho:
    """Write two counter increments with a configurable hold in between."""

    @workflow.run
    async def run(self, counter_path: str, correlation_id: str, hold_seconds: float = 8.0) -> str:
        workflow_id = workflow.info().workflow_id
        await workflow.execute_activity(
            increment_counter,
            args=[counter_path, "first", idempotency_key(workflow_id, "first"), correlation_id],
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        await workflow.sleep(hold_seconds)
        await workflow.execute_activity(
            increment_counter,
            args=[counter_path, "second", idempotency_key(workflow_id, "second"), correlation_id],
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        return workflow_id


SAMPLE = WorkerSet(
    task_queue=TaskQueue.EXPERIMENTS,
    workflows=[DurableEcho],
    activities=[increment_counter],
)

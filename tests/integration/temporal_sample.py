"""Sample durable workflow and activity for the W0.7 restart-survival test.

Deliberately phase-free: :class:`DurableEcho` increments an external counter (a
file), holds for a while, then increments it again through the same activity.
The hold window is what ``test_temporal.py`` uses to kill the worker mid-run.

Both steps follow the platform conventions in :mod:`plm_platform.temporal`: the
workflow id is the business key, the activity receives a deterministic
``idempotency_key`` per step and skips an increment it has already recorded.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from plm_platform.temporal import DEFAULT_RETRY_POLICY, TaskQueue, WorkerSet, idempotency_key
from temporalio import activity, workflow

__all__ = ["SAMPLE", "DurableEcho", "increment_counter"]

ACTIVITY_TIMEOUT = timedelta(seconds=30)


@activity.defn
async def increment_counter(counter_path: str, marker: str, idempotency_key: str) -> int:
    """Append ``marker`` once per ``idempotency_key``; return the recorded count."""
    return await asyncio.to_thread(_increment_once, Path(counter_path), marker, idempotency_key)


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
    async def run(self, counter_path: str, hold_seconds: float = 8.0) -> str:
        workflow_id = workflow.info().workflow_id
        await workflow.execute_activity(
            increment_counter,
            args=[counter_path, "first", idempotency_key(workflow_id, "first")],
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        await workflow.sleep(hold_seconds)
        await workflow.execute_activity(
            increment_counter,
            args=[counter_path, "second", idempotency_key(workflow_id, "second")],
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        return workflow_id


SAMPLE = WorkerSet(
    task_queue=TaskQueue.EXPERIMENTS,
    workflows=[DurableEcho],
    activities=[increment_counter],
)

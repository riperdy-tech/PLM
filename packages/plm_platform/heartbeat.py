"""Platform-level worker heartbeat workflow (W0.7).

Temporal rejects a worker that registers no workflows, activities or Nexus
services, and Wave 0 ships no phase registrations yet; this tiny workflow keeps
a fresh-checkout worker process legal and doubles as a liveness probe: start it
under a business key and it echoes that key back after an optional hold.

It is platform machinery, never phase business logic, and lives in its own
module so the workflow sandbox only ever imports ``temporalio.workflow``.
"""

from __future__ import annotations

from temporalio import workflow

__all__ = ["PlatformHeartbeat"]


@workflow.defn
class PlatformHeartbeat:
    """Echo the business workflow id after an optional hold; no side effects."""

    @workflow.run
    async def run(self, hold_seconds: float = 0.0) -> str:
        if hold_seconds > 0:
            await workflow.sleep(hold_seconds)
        return workflow.info().workflow_id

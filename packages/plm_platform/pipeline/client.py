"""Starting a pipeline run: one durable run per P2 directive, never two.

The workflow id is the business key ``business_workflow_id("pipeline", directive_id)``
and the start refuses any id that was ever used (``REJECT_DUPLICATE``): a directive's
budget reservations, effects and hold subjects are keyed by that id, so a second run
would replay the first run's ledger entries and provider effects instead of making its
own. (The store refuses it independently: a second run's first transition conflicts
with the first run's history.) A directive that must be produced again is a new P2
directive with its own id.
"""

from __future__ import annotations

from typing import Any

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy

from ..temporal import TaskQueue, business_workflow_id
from .types import PipelineInput, PipelineOptions
from .workflow import PipelineWorkflow

__all__ = ["pipeline_workflow_id", "start_pipeline"]


def pipeline_workflow_id(directive_id: str) -> str:
    return business_workflow_id("pipeline", directive_id)


async def start_pipeline(
    client: Client,
    *,
    directive_id: str,
    brand_id: str,
    task_queue: TaskQueue = TaskQueue.PIPELINE,
    options: PipelineOptions | None = None,
) -> WorkflowHandle[Any, Any]:
    """Start the run for one directive; raises if the directive ever had a run."""
    pipeline_input = PipelineInput(directive_id=directive_id, brand_id=brand_id,
                                   options=options if options is not None else PipelineOptions())
    return await client.start_workflow(
        PipelineWorkflow.run, pipeline_input, id=pipeline_workflow_id(directive_id),
        task_queue=task_queue.value, id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )

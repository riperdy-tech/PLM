"""The compose ``pipeline-worker`` runs the workflow with HTTP activities.

Proves the deployed wiring only: the worker polls ``pipeline``, its research step reaches
the running ``api-ar`` by compose service name with the service token, and a directive
P2 does not queue fails the run closed (``PolicyRejection``, recorded in ``ops``) instead
of inventing an experiment. It does not prove the P3/P4 steps over HTTP; their request
shapes are unit-tested and a real directive end to end is Gate R5.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from plm_platform.pipeline.client import start_pipeline
from plm_platform.pipeline.states import PipelineState
from plm_platform.pipeline.types import PipelineOutcome
from plm_platform.temporal import TaskQueue, queue_is_polling
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from tests.integration.pipeline.support import transitions

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_deployed_worker_fails_an_unqueued_directive_closed_over_http(
    client: Client, factory: sessionmaker[Session],
) -> None:
    if not await queue_is_polling(client, TaskQueue.PIPELINE):
        pytest.skip("no pipeline-worker polls `pipeline`: docker compose up -d --wait "
                    "pipeline-worker")
    directive_id = f"gate-unqueued-{uuid.uuid4().hex[:12]}"
    handle = await start_pipeline(client, directive_id=directive_id, brand_id="neon_seoul")
    workflow_id = handle.id
    outcome: PipelineOutcome = await asyncio.wait_for(handle.result(), 90)

    assert (outcome.final_state, outcome.completed) == ("FAILED", False)
    assert outcome.error_code == "PolicyRejection"
    rows = transitions(factory, workflow_id)
    assert [t.to_state for t in rows] == [PipelineState.IDEA, PipelineState.FAILED]
    failed = rows[-1]
    assert failed.step == "research" and failed.final
    assert failed.error_detail is not None and "not queued" in failed.error_detail
    assert failed.attempt == 1  # a definite refusal is not retried

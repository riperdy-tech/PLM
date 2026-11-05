"""How the pipeline workflow reads a failed step activity (no Temporal server)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from plm_platform.pipeline.states import Step
from plm_platform.pipeline.workflow import PipelineWorkflow, _Failure, _failure
from temporalio.exceptions import ActivityError, ApplicationError, RetryState


def _error(retry_state: RetryState, *, non_retryable: bool = False) -> ActivityError:
    error = ActivityError("activity failed", scheduled_event_id=1, started_event_id=2,
                          identity="worker", activity_type="pipeline.step.generate",
                          activity_id="7", retry_state=retry_state)
    error.__cause__ = ApplicationError("boom", type="TransientStepError",
                                       non_retryable=non_retryable)
    return error


@pytest.mark.parametrize(("retry_state", "exhausted"), [
    (RetryState.MAXIMUM_ATTEMPTS_REACHED, True),
    (RetryState.TIMEOUT, True),
    (RetryState.NON_RETRYABLE_FAILURE, False),
    (RetryState.CANCEL_REQUESTED, False),
])
def test_retries_ran_out_when_attempts_or_time_did(retry_state: RetryState,
                                                    exhausted: bool) -> None:
    failure = _failure(_error(retry_state))
    assert (failure.type, failure.retries_exhausted) == ("TransientStepError", exhausted)


def test_thumbnail_hold_keeps_exact_package_ids_for_studio(monkeypatch: pytest.MonkeyPatch) -> None:
    """The structured P6 package IDs survive the workflow's exception-to-hold seam."""
    monkeypatch.setattr(
        "plm_platform.pipeline.workflow.workflow.info",
        lambda: SimpleNamespace(workflow_id="pipeline.example"))
    workflow = PipelineWorkflow()
    recorded: list[dict[str, Any]] = []

    async def record_hold(step: Step, specs: list[tuple[str, str, dict[str, Any]]],
                          **kwargs: Any) -> bool:
        assert step == Step.ASSEMBLE
        recorded.append(specs[0][2])
        return True

    monkeypatch.setattr(workflow, "_hold", record_hold)
    failure = _Failure(
        type="ExternalWriteRefused", message="thumbnail art awaited", attempt=1,
        non_retryable=True,
        details={"hold_reason_code": "THUMBNAIL_ART_AWAITED",
                 "media_package_ids": ["mpkg_aaaaaaaa", "mpkg_bbbbbbbb"]})
    assert asyncio.run(workflow._write_hold(Step.ASSEMBLE, failure)) is True
    assert recorded[0]["media_package_ids"] == ["mpkg_aaaaaaaa", "mpkg_bbbbbbbb"]

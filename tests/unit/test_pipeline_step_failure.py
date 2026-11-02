"""How the pipeline workflow reads a failed step activity (no Temporal server)."""

from __future__ import annotations

import pytest
from plm_platform.pipeline.workflow import _failure
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

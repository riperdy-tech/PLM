"""W0.7 unit tests: the task-queue registry and workflow conventions are single-sourced.

These tests are the drift guard for the conventions in ``plm_platform.temporal``:
if a queue name, an id rule or a retry-policy value changes, they fail loudly.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from plm_platform.temporal import (
    DEFAULT_RETRY_POLICY,
    NON_RETRYABLE_ERROR_TYPES,
    CreativeRejection,
    NonRetryableError,
    Phase,
    PolicyRejection,
    TaskQueue,
    ValidationFailure,
    all_task_queues,
    business_workflow_id,
    idempotency_key,
    task_queue,
)

EXPECTED_QUEUES = (
    "ph1.market",
    "ph2.ar",
    "ph3.experiments",
    "ph4.music",
    "ph5.qa",
    "ph6.media",
    "ph7.youtube",
    "ph8.distribution",
    "ph9.analytics",
)


def test_task_queue_members_are_exactly_the_nine_documented_names() -> None:
    assert tuple(queue.value for queue in TaskQueue) == EXPECTED_QUEUES
    assert len(set(EXPECTED_QUEUES)) == len(EXPECTED_QUEUES)


def test_every_phase_has_exactly_one_queue_and_pipeline_order_holds() -> None:
    assert tuple(queue.value for queue in all_task_queues()) == EXPECTED_QUEUES
    for phase in Phase:
        assert task_queue(phase).value.startswith(f"{phase.value}.")


def test_business_workflow_id_is_deterministic_and_validated() -> None:
    assert business_workflow_id("music", "track", "track_01HQ") == "music.track.track_01HQ"
    assert business_workflow_id("solo") == "solo"
    for bad in ("", "a.b", "a:b", "a b"):
        with pytest.raises(ValueError):
            business_workflow_id("ok", bad)
    with pytest.raises(ValueError):
        business_workflow_id()


def test_idempotency_key_is_step_scoped_and_never_clock_based() -> None:
    workflow_id = business_workflow_id("youtube", "publish", "vid-1")
    assert idempotency_key(workflow_id, "upload") == f"{workflow_id}:upload"
    assert idempotency_key(workflow_id, "upload") == idempotency_key(workflow_id, "upload")
    assert idempotency_key(workflow_id, "upload") != idempotency_key(workflow_id, "thumbnail")
    with pytest.raises(ValueError):
        idempotency_key("", "upload")
    with pytest.raises(ValueError):
        idempotency_key(workflow_id, "")
    with pytest.raises(ValueError):
        idempotency_key(workflow_id, "up:load")


def test_default_retry_policy_matches_the_documented_table() -> None:
    assert DEFAULT_RETRY_POLICY.initial_interval == timedelta(seconds=1)
    assert DEFAULT_RETRY_POLICY.backoff_coefficient == 2.0
    assert DEFAULT_RETRY_POLICY.maximum_interval == timedelta(seconds=60)
    assert DEFAULT_RETRY_POLICY.maximum_attempts == 5
    assert list(DEFAULT_RETRY_POLICY.non_retryable_error_types or []) == list(
        NON_RETRYABLE_ERROR_TYPES
    )


def test_non_retryable_policy_list_names_the_error_classes() -> None:
    classes = (ValidationFailure, PolicyRejection, CreativeRejection)
    assert tuple(cls.__name__ for cls in classes) == NON_RETRYABLE_ERROR_TYPES
    for cls in classes:
        assert issubclass(cls, NonRetryableError)

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
    CapabilityUnavailable,
    CreativeRejection,
    NonRetryableError,
    Phase,
    PolicyRejection,
    TaskQueue,
    ValidationFailure,
    all_task_queues,
    business_workflow_id,
    idempotency_key,
    platform_task_queues,
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


EXPECTED_PLATFORM_QUEUES = ("pipeline", "pipeline.acceptance")
"""R3: the pipeline workflow's queue, and the Gate R3 acceptance queue no deployed worker
polls (so a test run with controlled activities never reaches the compose worker)."""


def test_task_queue_members_are_the_nine_phase_names_then_the_platform_queues() -> None:
    assert tuple(queue.value for queue in TaskQueue) == (
        EXPECTED_QUEUES + EXPECTED_PLATFORM_QUEUES
    )
    assert len(set(TaskQueue)) == len(EXPECTED_QUEUES) + len(EXPECTED_PLATFORM_QUEUES)


def test_only_the_pipeline_queue_is_a_deployed_platform_queue() -> None:
    assert tuple(queue.value for queue in platform_task_queues()) == ("pipeline",)
    assert TaskQueue.PIPELINE_ACCEPTANCE not in platform_task_queues()
    assert TaskQueue.PIPELINE_ACCEPTANCE not in all_task_queues()


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
    classes = (ValidationFailure, PolicyRejection, CreativeRejection, CapabilityUnavailable)
    assert tuple(cls.__name__ for cls in classes) == NON_RETRYABLE_ERROR_TYPES
    for cls in classes:
        assert issubclass(cls, NonRetryableError)


def test_every_non_retryable_subclass_in_this_module_is_listed_by_name() -> None:
    """Temporal matches non-retryable types by exact class name, not by inheritance."""
    from plm_platform import temporal

    subclasses = {
        name for name, value in vars(temporal).items()
        if isinstance(value, type) and issubclass(value, NonRetryableError)
        and value is not NonRetryableError
    }
    assert subclasses == set(NON_RETRYABLE_ERROR_TYPES)


def test_capability_unavailable_names_its_owning_stage() -> None:
    error = CapabilityUnavailable("pipeline.qa", owning_stage="R4", reason="no audio by URI")
    assert error.owning_stage == "R4"
    assert "R4" in str(error)
    with pytest.raises(ValueError):
        CapabilityUnavailable("pipeline.qa", owning_stage="", reason="x")

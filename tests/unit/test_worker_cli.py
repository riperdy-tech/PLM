"""W0.7 unit tests: the worker entry point's parsing and registration seams.

Everything here runs without a Temporal server; the durable behaviour is covered
by ``tests/integration/test_temporal.py``.
"""

from __future__ import annotations

import argparse

import pytest
from plm_platform.heartbeat import PlatformHeartbeat
from plm_platform.temporal import TaskQueue, WorkerSet
from plm_platform.worker import (
    BUILTIN_WORKFLOWS,
    load_worker_sets,
    parse_task_queue,
    registrations_for,
)

SAMPLE_SPEC = "tests.integration.temporal_sample:SAMPLE"


def test_parse_task_queue_accepts_queue_values_and_phase_tokens() -> None:
    assert parse_task_queue("ph4.music") is TaskQueue.MUSIC
    assert parse_task_queue("ph4") is TaskQueue.MUSIC
    with pytest.raises(argparse.ArgumentTypeError):
        parse_task_queue("ph10.nope")


def test_load_worker_sets_resolves_the_sample_registration() -> None:
    sets = load_worker_sets(SAMPLE_SPEC)
    assert len(sets) == 1
    sample = sets[0]
    assert sample.task_queue is TaskQueue.EXPERIMENTS
    assert sample.workflows
    assert sample.activities


def test_load_worker_sets_reports_bad_specs() -> None:
    with pytest.raises(ValueError, match="module:attribute"):
        load_worker_sets("tests.integration.temporal_sample")
    with pytest.raises(ValueError, match="has no attribute"):
        load_worker_sets("tests.integration.temporal_sample:Missing")
    with pytest.raises(ValueError, match="WorkerSet"):
        load_worker_sets("tests.integration.test_temporal:REPO_ROOT")


def test_registrations_always_include_the_platform_heartbeat() -> None:
    workflows, activities = registrations_for(TaskQueue.EXPERIMENTS, ())
    assert workflows == BUILTIN_WORKFLOWS
    assert activities == ()
    assert PlatformHeartbeat in workflows


def test_registrations_merge_selected_sets_without_duplicates() -> None:
    sets = load_worker_sets(SAMPLE_SPEC)
    workflows, activities = registrations_for(TaskQueue.EXPERIMENTS, sets * 2)
    sample = sets[0]
    assert tuple(workflows) == BUILTIN_WORKFLOWS + tuple(sample.workflows)
    assert tuple(activities) == tuple(sample.activities)
    other = registrations_for(TaskQueue.MUSIC, sets)
    assert other == (BUILTIN_WORKFLOWS, ())


def test_worker_set_rejects_a_non_task_queue() -> None:
    with pytest.raises(TypeError):
        WorkerSet(task_queue="ph4.music")  # type: ignore[arg-type]

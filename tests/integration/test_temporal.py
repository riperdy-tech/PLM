"""W0.7 integration: a sample durable workflow survives a worker restart mid-run.

Requires Temporal (docker compose). Skips with an explicit reason when it is
unreachable; start it with ``docker compose up -d --wait temporal temporal-ui``.

The dev compose worker serves ``ph1.market`` only, so the ``ph3.experiments``
queue used here has exactly one owner at a time: the worker subprocess this
test starts, kills and restarts. The healthcheck negative test uses
``ph6.media``, which nothing in this repository polls.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from plm_platform.config import Settings, SettingsError, load_settings
from plm_platform.temporal import (
    TaskQueue,
    business_workflow_id,
    connect_client,
    queue_is_polling,
)
from temporalio.client import Client, WorkflowExecutionStatus

from tests.integration.temporal_sample import DurableEcho

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE = TaskQueue.EXPERIMENTS
HEALTHCHECK_QUEUE = TaskQueue.MEDIA  # nothing polls this queue in the suite; see the docstring
HOLD_SECONDS = 8.0
CLIENT_CONNECT_TIMEOUT = 10.0
RESULT_TIMEOUT = 60.0
POLLER_TIMEOUT = 45.0
MARKER_TIMEOUT = 30.0


class WorkerProcess:
    """A worker subprocess whose stdout/stderr land in a log file for diagnosis."""

    def __init__(self, identity: str, log_path: Path) -> None:
        self.identity = identity
        self.log_path = log_path
        self._log_file = log_path.open("ab")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "plm_platform.worker",
                "--task-queue",
                QUEUE.value,
                "--load",
                "tests.integration.temporal_sample:SAMPLE",
                "--identity",
                identity,
            ],
            cwd=REPO_ROOT,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )

    async def wait_until_polling(self, client: Client) -> None:
        deadline = time.monotonic() + POLLER_TIMEOUT
        while not await queue_is_polling(client, QUEUE, identity_prefix=self.identity):
            if self.process.poll() is not None:
                raise AssertionError(
                    f"worker exited rc={self.process.returncode} before polling "
                    f"{QUEUE.value}\n{self.log_tail()}"
                )
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"worker {self.identity} never polled {QUEUE.value}\n{self.log_tail()}"
                )
            await asyncio.sleep(0.25)

    async def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            await asyncio.to_thread(self.process.wait)
        if not self._log_file.closed:
            self._log_file.close()

    def log_tail(self, lines: int = 40) -> str:
        try:
            content = self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return "<no worker log>"
        return "\n".join(content.splitlines()[-lines:])


def _markers(counter_path: Path) -> list[str]:
    """The marker column of the counter file, in write order."""
    if not counter_path.exists():
        return []
    return [
        line.split("\t", 1)[0]
        for line in counter_path.read_text(encoding="utf-8").splitlines()
        if line
    ]


async def _wait_for_markers(counter_path: Path, expected: list[str]) -> None:
    async with asyncio.timeout(MARKER_TIMEOUT):
        while _markers(counter_path) != expected:
            await asyncio.sleep(0.05)


@pytest.fixture
def settings() -> Settings:
    try:
        return load_settings(dotenv_path=REPO_ROOT / ".env")
    except SettingsError as exc:
        pytest.skip(f"no usable configuration: {exc} - copy .env.example to .env")


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[Client]:
    try:
        async with asyncio.timeout(CLIENT_CONNECT_TIMEOUT):
            temporal_client = await connect_client(settings)
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        pytest.skip(
            f"temporal not reachable at {settings.temporal_host} - "
            f"{type(exc).__name__}: {exc}; start it with: "
            "docker compose up -d --wait temporal temporal-ui"
        )
    yield temporal_client


async def test_workflow_survives_worker_restart_mid_run(client: Client, tmp_path: Path) -> None:
    counter = tmp_path / "counter.log"
    workflow_id = business_workflow_id("test", "durable-echo", uuid.uuid4().hex)
    first_worker = WorkerProcess(f"plm-it-a-{uuid.uuid4().hex[:8]}", tmp_path / "worker-a.log")
    second_worker: WorkerProcess | None = None
    try:
        await first_worker.wait_until_polling(client)
        handle = await client.start_workflow(
            DurableEcho.run,
            args=[str(counter), HOLD_SECONDS],
            id=workflow_id,
            task_queue=QUEUE.value,
        )
        run_id = handle.first_execution_run_id
        assert run_id is not None
        await _wait_for_markers(counter, ["first"])

        # Mid-run: the first increment is durable, the second has not happened.
        assert _markers(counter) == ["first"]
        await first_worker.kill()
        assert _markers(counter) == ["first"], "a killed worker kept writing"
        mid_run = await handle.describe()
        assert mid_run.status == WorkflowExecutionStatus.RUNNING
        assert mid_run.run_id == run_id

        second_worker = WorkerProcess(
            f"plm-it-b-{uuid.uuid4().hex[:8]}", tmp_path / "worker-b.log"
        )
        await second_worker.wait_until_polling(client)

        result = await asyncio.wait_for(handle.result(), timeout=RESULT_TIMEOUT)
        assert handle.result_run_id == run_id
        assert result == workflow_id

        # Exactly once: two distinct increments, no duplicate after the restart.
        assert _markers(counter) == ["first", "second"]
        final = await handle.describe()
        assert final.status == WorkflowExecutionStatus.COMPLETED
        assert final.run_id == run_id
    finally:
        await first_worker.kill()
        if second_worker is not None:
            await second_worker.kill()


async def test_healthcheck_fails_when_no_worker_polls_the_queue(client: Client) -> None:
    # Proves the compose worker healthcheck can actually fail: with no poller on
    # the queue it must exit non-zero (the old "python --version" never could).
    completed = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "plm_platform.worker",
            "--healthcheck",
            "--task-queue",
            HEALTHCHECK_QUEUE.value,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    assert "no pollers" in output, output

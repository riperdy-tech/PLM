"""Gate R3 support: the compose Temporal and PostgreSQL, a budget for "now", reviewers.

Every run uses a fresh directive id, so its workflow id, idempotency keys, hold subjects
and transition rows are its own. The append-only tables (``ops.pipeline_transitions``,
``ops.exception_events``) keep their rows by design; the ledger rows the tests create
(ceilings, rates and entries marked ``set_by = pipeline-gate-test``) are removed.

The in-process worker serves ``TaskQueue.PIPELINE_ACCEPTANCE``, which no deployed worker
polls, so the compose ``pipeline-worker`` (HTTP activities) never picks up a gate run.
Skips with the reason when Temporal or PostgreSQL is unreachable.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
import pytest_asyncio
import sqlalchemy as sa
from plm_contracts.money import Money
from plm_platform.config import Settings, SettingsError, load_settings
from plm_platform.db import build_engine, database_url
from plm_platform.exceptions import ExceptionQueue, StaticReviewerDirectory
from plm_platform.ledger import (
    GLOBAL_SCOPE,
    BudgetLedger,
    budget_ceilings,
    budget_entries,
    budget_rates,
)
from plm_platform.pipeline.activities import PlatformOperations, build_activities
from plm_platform.pipeline.client import pipeline_workflow_id, start_pipeline
from plm_platform.pipeline.mock import MockPhaseSteps
from plm_platform.pipeline.store import PipelineStore, StoredTransition
from plm_platform.pipeline.types import PipelineOptions, PipelineStatus
from plm_platform.pipeline.workflow import PipelineWorkflow
from plm_platform.temporal import TaskQueue, connect_client, create_worker
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client, WorkflowHandle
from temporalio.worker import Worker

REPO_ROOT = Path(__file__).resolve().parents[3]
QUEUE = TaskQueue.PIPELINE_ACCEPTANCE
SET_BY = "pipeline-gate-test"
CURRENCY = "USD"
RATES = {"music.generate": "1.00", "youtube.upload": "0.50", "distribution.submit": "1.00"}
REVIEWERS = {
    "rita": ["rights_reviewer"],
    "olga": ["budget_owner"],
    "admin": ["admin"],
    "counsel": ["rights_counsel"],
}
OPTIONS = PipelineOptions(step_start_to_close_seconds=120.0, step_heartbeat_seconds=5.0,
                          step_maximum_attempts=5, platform_start_to_close_seconds=30.0,
                          platform_maximum_attempts=5)
WAIT_SECONDS = 60.0


def _settings() -> Settings:
    try:
        return load_settings(dotenv_path=REPO_ROOT / ".env")
    except SettingsError as exc:
        pytest.skip(f"no usable configuration: {exc} - copy .env.example to .env")


@pytest.fixture(scope="module")
def settings() -> Settings:
    return _settings()


@pytest.fixture(scope="module")
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    url = database_url(settings)
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        pytest.skip(f"postgres not reachable at {url.host}:{url.port} - {exc}; "
                    "start it with: docker compose up -d --wait postgres")
    engine = build_engine(settings)
    assert sa.inspect(engine).has_table("pipeline_transitions", schema="ops"), (
        "ops.pipeline_transitions is missing: the integration tier assumes a migrated "
        "database (run `plm migrate`)"
    )
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[Client]:
    try:
        async with asyncio.timeout(10):
            temporal = await connect_client(settings)
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        pytest.skip(f"temporal not reachable at {settings.temporal_host} - {exc}; "
                    "start it with: docker compose up -d --wait temporal")
    yield temporal


def _cleanup_budget(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ours = sa.select(budget_ceilings.c.ceiling_id).where(budget_ceilings.c.set_by == SET_BY)
        session.execute(budget_entries.delete().where(
            budget_entries.c.ceiling_id.in_(ours)
            | budget_entries.c.capability_ceiling_id.in_(ours)
            | budget_entries.c.idempotency_key.like("pipeline.gate-%")))
        session.execute(budget_ceilings.delete().where(budget_ceilings.c.set_by == SET_BY))
        session.execute(budget_rates.delete().where(budget_rates.c.set_by == SET_BY))
        session.commit()


@dataclass
class Budget:
    ledger: BudgetLedger
    start: datetime
    end: datetime
    now: datetime

    def set_capability_ceiling(self, capability: str, amount: str) -> str:
        ceiling = self.ledger.set_ceiling(
            limit=Money.of(amount, CURRENCY), period_start=self.start, period_end=self.end,
            set_by=SET_BY, now=self.now, scope=f"capability:{capability}")
        return ceiling.ceiling_id


@pytest.fixture
def budget(factory: sessionmaker[Session]) -> Iterator[Budget]:
    """A global ceiling covering the Temporal server's "now" (workflow.now() is real time
    here) and a rate card for the three paid capabilities."""
    _cleanup_budget(factory)
    now = datetime.now(UTC)
    start = (now - timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(days=3)
    ledger = BudgetLedger(factory, currency=CURRENCY)
    ledger.set_ceiling(limit=Money.of("1000", CURRENCY), period_start=start, period_end=end,
                       set_by=SET_BY, now=now, scope=GLOBAL_SCOPE)
    for capability, amount in RATES.items():
        ledger.set_rate(capability=capability, cost=Money.of(amount, CURRENCY),
                        effective_from=start, set_by=SET_BY, now=now)
    try:
        yield Budget(ledger=ledger, start=start, end=end, now=now)
    finally:
        _cleanup_budget(factory)


@pytest.fixture
def platform(factory: sessionmaker[Session]) -> PlatformOperations:
    return PlatformOperations(
        factory, queue=ExceptionQueue(reviewers=StaticReviewerDirectory(REVIEWERS)),
        ledger=BudgetLedger(factory, currency=CURRENCY))


def new_run() -> tuple[str, str]:
    """(directive_id, workflow_id) for a fresh run."""
    directive_id = f"gate-{uuid.uuid4().hex[:16]}"
    return directive_id, pipeline_workflow_id(directive_id)


def worker_for(client: Client, steps: MockPhaseSteps, platform: PlatformOperations,
               identity: str | None = None) -> Worker:
    options: dict[str, Any] = {} if identity is None else {"identity": identity}
    return create_worker(client, QUEUE, workflows=[PipelineWorkflow],
                         activities=build_activities(steps, platform), **options)


@asynccontextmanager
async def running(worker: Worker) -> AsyncIterator[Worker]:
    task = asyncio.create_task(worker.run())
    try:
        yield worker
    finally:
        await worker.shutdown()
        await asyncio.gather(task, return_exceptions=True)


async def start_run(client: Client, directive_id: str, workflow_id: str
                    ) -> WorkflowHandle[Any, Any]:
    handle = await start_pipeline(client, directive_id=directive_id, brand_id="gate_brand",
                                  task_queue=QUEUE, options=OPTIONS)
    assert handle.id == workflow_id
    return handle


async def status(handle: WorkflowHandle[Any, Any]) -> PipelineStatus:
    result: PipelineStatus = await handle.query(PipelineWorkflow.status)
    return result


async def wait_until(handle: WorkflowHandle[Any, Any],
                     predicate: Callable[[PipelineStatus], bool],
                     what: str) -> PipelineStatus:
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
    last: PipelineStatus | None = None
    while asyncio.get_running_loop().time() < deadline:
        last = await status(handle)
        if predicate(last):
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}; last status {last}")


async def signal_and_wait(handle: WorkflowHandle[Any, Any],
                          send: Callable[[], Awaitable[None]]) -> PipelineStatus:
    """Send a signal and wait until the workflow has recorded its decision."""
    before = (await status(handle)).sequence
    await send()
    return await wait_until(handle, lambda s: s.sequence > before, "the signal's decision")


def transitions(factory: sessionmaker[Session], workflow_id: str) -> list[StoredTransition]:
    with factory() as session:
        return PipelineStore().transitions(session, workflow_id)

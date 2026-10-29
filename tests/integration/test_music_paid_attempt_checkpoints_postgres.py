"""B03/B04 paid-attempt checkpoints on an explicitly isolated PostgreSQL database.

Requires PLM_MUSIC_PG_ISOLATED=1 and a loopback PostgreSQL role allowed to create a
disposable database; the configured application database is never written. Fake
providers only: this proves the durable SUBMITTED/settled rows and the P4 CHECK
constraints on PostgreSQL, not any real provider's billing.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from plm_platform.clock import FixedClock
from plm_platform.config import load_settings
from plm_platform.db import database_url
from plm_platform.exceptions import EXCEPTIONS_METADATA
from sqlalchemy.orm import Session

from services.music_factory.domain.enums import AttemptStatus, PlanStatus
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.models import MUSIC_METADATA
from services.music_factory.storage.repositories import list_generation_attempts
from tests.unit.music.test_paid_attempt_checkpoints import SecondCallRaises
from tests.unit.music.test_write_guard_and_idempotency import (
    RecordingBudget,
    _live_guard,
    _spec,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def isolated_music_db() -> Iterator[sa.Engine]:
    if os.getenv("PLM_MUSIC_PG_ISOLATED") != "1":
        pytest.skip("PLM_MUSIC_PG_ISOLATED=1 required for a disposable PostgreSQL DB")
    url = database_url(load_settings(dotenv_path=Path(".env")))
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("requires a loopback PostgreSQL endpoint for a disposable DB")
    root = sa.create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
    name = f"plm_music_checkpoint_probe_{uuid4().hex[:16]}"
    created = False
    probe: sa.Engine | None = None
    try:
        with root.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created = True
        probe = sa.create_engine(url.set(database=name), connect_args={"connect_timeout": 5})
        with probe.begin() as conn:
            conn.exec_driver_sql("CREATE SCHEMA music")
            conn.exec_driver_sql("CREATE SCHEMA ops")
        MUSIC_METADATA.create_all(probe)
        EXCEPTIONS_METADATA.create_all(probe)
        yield probe
    finally:
        if probe is not None:
            probe.dispose()
        if created:
            with root.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        root.dispose()


def test_paid_attempts_survive_a_failed_request_on_postgres(
    isolated_music_db: sa.Engine,
) -> None:
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    provider = SecondCallRaises()

    def run() -> object:
        engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                                  write_guard=_live_guard(RecordingBudget()))
        with Session(isolated_music_db) as session:
            try:
                return engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                                           provider_adapter=provider,
                                           checkpoint=session.commit)
            finally:
                session.rollback()

    with pytest.raises(ConnectionError):
        run()
    with Session(isolated_music_db) as session:
        attempts = sorted(list_generation_attempts(session, plan.plan_id),
                          key=lambda a: a.attempt_id)
    assert [(a.status, bool(a.provider_job_id.strip()), a.credit_basis) for a in attempts] == [
        (AttemptStatus.SUCCEEDED, True, "provider_reported"),
        (AttemptStatus.SUBMITTED, False, "unknown"),
    ]
    replay = run()
    assert provider.calls == 2
    assert replay.status is PlanStatus.FAILED  # type: ignore[attr-defined]
    assert "provider_job_reconciliation_required" in replay.errors  # type: ignore[attr-defined]

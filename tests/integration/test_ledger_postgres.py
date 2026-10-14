"""R3 integration: the cost ledger's row locking against the compose PostgreSQL.

What this proves that the unit tier cannot: on PostgreSQL, two reservations racing for
the last headroom of a ceiling are serialised by ``SELECT ... FOR UPDATE`` on the ceiling
row, so exactly one passes; and a replayed key racing its original never reserves twice.
The tables come from the real alembic chain (``0027_ops_cost_ledger``); like the
rest of the integration tier it assumes a migrated database (``plm migrate``).

Skips with an explicit reason when postgres is unreachable (``docker compose up -d
--wait postgres``). It writes only rows for its own ceiling, in a period far from any
real one, and deletes them afterwards.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
import sqlalchemy as sa
from plm_contracts.money import Money
from plm_platform.config import Settings, load_settings
from plm_platform.db import build_engine, database_url
from plm_platform.ledger import (
    GLOBAL_SCOPE,
    BudgetLedger,
    Reason,
    budget_ceilings,
    budget_entries,
)
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PERIOD_START = datetime(2099, 1, 1, tzinfo=UTC)
PERIOD_END = datetime(2099, 2, 1, tzinfo=UTC)
NOW = datetime(2099, 1, 15, 12, 0, tzinfo=UTC)
CEILING_ID = f"{GLOBAL_SCOPE}@{PERIOD_START.isoformat()}"


def _settings() -> Settings:
    return load_settings(dotenv_path=REPO_ROOT / ".env")


def _skip_reason() -> str | None:
    url = database_url(_settings())
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        return (
            f"postgres not reachable at {url.host}:{url.port} - {type(exc).__name__}: {exc}; "
            "start it with: docker compose up -d --wait postgres"
        )
    return None


def _cleanup(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(budget_entries.delete().where(budget_entries.c.ceiling_id == CEILING_ID))
        conn.execute(budget_ceilings.delete().where(budget_ceilings.c.ceiling_id == CEILING_ID))


@pytest.fixture(scope="module")
def engine() -> Iterator[sa.Engine]:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)
    built = build_engine(_settings())
    assert sa.inspect(built).has_table("budget_entries", schema="ops"), (
        "ops.budget_entries is missing: the integration tier assumes a migrated database "
        "(run `plm migrate`)"
    )
    _cleanup(built)
    try:
        yield built
    finally:
        _cleanup(built)
        built.dispose()


@pytest.fixture
def ledger(engine: sa.Engine) -> Iterator[BudgetLedger]:
    _cleanup(engine)
    ledger = BudgetLedger(sessionmaker(bind=engine), currency="USD")
    ledger.set_ceiling(
        limit=Money.of("5", "USD"), period_start=PERIOD_START, period_end=PERIOD_END,
        set_by="integration@test", now=NOW,
    )
    yield ledger
    _cleanup(engine)


def _slow_entry_reads(engine: sa.Engine) -> Any:
    def _delay(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if "budget_entries" in statement and statement.lstrip().upper().startswith("SELECT"):
            time.sleep(0.1)

    return _delay


def _warm_pool(engine: sa.Engine) -> None:
    """Open two pooled connections up front: a connection set up inside the race would
    take longer than the race window and serialise the threads by accident."""
    with engine.connect() as first, engine.connect() as second:
        first.execute(sa.text("SELECT 1"))
        second.execute(sa.text("SELECT 1"))


def _race(ledger: BudgetLedger, keys: tuple[str, str]) -> list[Any]:
    barrier = threading.Barrier(2)
    results: list[Any] = [None, None]

    def run(i: int) -> None:
        barrier.wait()
        results[i] = ledger.reserve(
            "distribution.submit", keys[i], cost=Money.of("4", "USD"), now=NOW
        )

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_two_sessions_racing_for_the_last_headroom_exactly_one_passes(
    engine: sa.Engine, ledger: BudgetLedger
) -> None:
    delay = _slow_entry_reads(engine)
    _warm_pool(engine)
    sa.event.listen(engine, "before_cursor_execute", delay)
    try:
        results = _race(ledger, ("race-a", "race-b"))
    finally:
        sa.event.remove(engine, "before_cursor_execute", delay)
    assert sorted(r.allowed for r in results) == [False, True]
    assert {r.reason for r in results} == {Reason.RESERVED, Reason.EXHAUSTED}
    status = ledger.ceiling_status(now=NOW)
    assert status is not None and status.remaining.amount == Decimal(1)


def test_a_replay_racing_its_original_reserves_once(
    engine: sa.Engine, ledger: BudgetLedger
) -> None:
    delay = _slow_entry_reads(engine)
    _warm_pool(engine)
    sa.event.listen(engine, "before_cursor_execute", delay)
    try:
        results = _race(ledger, ("same-key", "same-key"))
    finally:
        sa.event.remove(engine, "before_cursor_execute", delay)
    assert all(r.allowed for r in results)
    assert sorted(r.replayed for r in results) == [False, True]
    ours = [
        e for e in ledger.entries(capability="distribution.submit", limit=10_000)
        if e.ceiling_id == CEILING_ID
    ]
    assert len(ours) == 1
    status = ledger.ceiling_status(now=NOW)
    assert status is not None and status.remaining.amount == Decimal(1)

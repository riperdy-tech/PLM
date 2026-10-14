"""SQLite `ops` schema harness for the cost ledger unit tests (R3, ADR-0033).

In-memory by default (one shared connection). With ``directory`` it is file-based, so
several threads hold separate connections and contend for SQLite's write lock the way
two service processes would.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlalchemy as sa
from plm_platform.ledger import LEDGER_METADATA
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["create_ops_engine", "ops_session_factory"]


def create_ops_engine(directory: Path | None = None, *, with_tables: bool = True) -> sa.Engine:
    """SQLite with an attached `ops` schema and (by default) the ledger tables created."""
    if directory is None:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
        ops_target = ":memory:"
    else:
        engine = sa.create_engine(
            f"sqlite+pysqlite:///{(directory / 'main.db').as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        ops_target = (directory / "ops.db").as_posix()

    @sa.event.listens_for(engine, "connect")
    def _attach(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute(f"ATTACH DATABASE '{ops_target}' AS ops")

    if with_tables:
        LEDGER_METADATA.create_all(engine)
    return engine


def ops_session_factory(engine: sa.Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)

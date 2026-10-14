# tests/support/ar_db.py
"""SQLite `ar` schema for unit tests — the same pattern as `market_db`, no docker."""

from __future__ import annotations

import sqlalchemy as sa
from plm_platform.decision_log import DECISION_LOG_METADATA
from sqlalchemy.orm import Session, sessionmaker

from services.autonomous_ar.storage.models import AR_METADATA

__all__ = ["ar_session", "create_ar_engine"]


def create_ar_engine(*, static_pool: bool = False) -> sa.Engine:
    """In-memory SQLite with attached `ar` and `ops` schemas and all P2 tables created.

    `static_pool=True` shares one connection across threads — required when the engine
    sits behind a `TestClient`, whose sync endpoints run in a worker thread: an in-memory
    database is per-connection, so a thread-local pool would see an empty schema.
    """
    if static_pool:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:", poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ar")
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ops")
    AR_METADATA.create_all(engine)
    DECISION_LOG_METADATA.create_all(engine)  # the cycle records PART XXI decisions
    return engine


def ar_session(engine: sa.Engine) -> Session:
    """A session bound to the engine (committed on clean exit by callers as needed)."""
    return sessionmaker(bind=engine)()

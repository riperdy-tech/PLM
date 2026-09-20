# tests/support/ar_db.py
"""SQLite `ar` schema for unit tests — the same pattern as `market_db`, no docker."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from services.autonomous_ar.storage.models import AR_METADATA

__all__ = ["ar_session", "create_ar_engine"]


def create_ar_engine() -> sa.Engine:
    """In-memory SQLite with an attached `ar` schema and all P2 tables created."""
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ar")
    AR_METADATA.create_all(engine)
    return engine


def ar_session(engine: sa.Engine) -> Session:
    """A session bound to the engine (committed on clean exit by callers as needed)."""
    return sessionmaker(bind=engine)()

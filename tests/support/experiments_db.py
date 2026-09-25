"""SQLite `experiments` schema for unit tests — the same pattern as `ar_db`, no docker."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from services.experiments.storage.models import EXPERIMENTS_METADATA

__all__ = ["create_experiments_engine", "experiments_session"]


def create_experiments_engine(*, static_pool: bool = False) -> sa.Engine:
    """In-memory SQLite with an attached `experiments` schema and all P3 tables created."""
    if static_pool:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS experiments")
    EXPERIMENTS_METADATA.create_all(engine)
    return engine


def experiments_session(engine: sa.Engine) -> Session:
    """A session bound to the engine."""
    return sessionmaker(bind=engine)()

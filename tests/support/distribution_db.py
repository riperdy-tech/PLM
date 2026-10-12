"""SQLite `distribution` schema harness for unit tests (Phase P8)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from distribution.storage.models import DISTRIBUTION_METADATA
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["create_distribution_engine", "distribution_db_session", "distribution_session"]


def create_distribution_engine(*, static_pool: bool = False) -> sa.Engine:
    """In-memory SQLite with an attached `distribution` schema and all P8 tables created."""
    if static_pool:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS distribution")
    DISTRIBUTION_METADATA.create_all(engine)
    return engine


def distribution_session(engine: sa.Engine) -> Session:
    """A session bound to the distribution engine."""
    return sessionmaker(bind=engine)()


@contextmanager
def distribution_db_session() -> Generator[Session, None, None]:
    """Provide a transactional in-memory distribution SQLite session."""
    engine = create_distribution_engine(static_pool=True)
    session = distribution_session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()

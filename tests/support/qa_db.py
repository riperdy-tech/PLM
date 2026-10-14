"""SQLite `qa` schema harness for unit tests.

P5 raises its holds into the platform exception queue in the same transaction, so the
harness also attaches `ops` and creates the exception-queue tables.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from plm_platform.exceptions import EXCEPTIONS_METADATA
from quality_gate.storage.models import QA_METADATA
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["create_qa_engine", "qa_db_session", "qa_session"]


def create_qa_engine(*, static_pool: bool = False) -> sa.Engine:
    """In-memory SQLite with attached `qa` and `ops` schemas, P5 and queue tables created."""
    if static_pool:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS qa")
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ops")
    QA_METADATA.create_all(engine)
    EXCEPTIONS_METADATA.create_all(engine)
    return engine


def qa_session(engine: sa.Engine) -> Session:
    """A session bound to the engine."""
    return sessionmaker(bind=engine)()


@contextmanager
def qa_db_session() -> Generator[Session, None, None]:
    """Provide a transactional in-memory qa SQLite session."""
    engine = create_qa_engine(static_pool=True)
    session = qa_session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()

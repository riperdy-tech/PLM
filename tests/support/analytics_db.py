"""SQLite `analytics` schema harness for unit tests (Phase P9)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from analytics.storage.models import ANALYTICS_METADATA
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["analytics_db_session", "analytics_session", "create_analytics_engine"]


def create_analytics_engine(*, static_pool: bool = False) -> sa.Engine:
    """In-memory SQLite with an attached `analytics` schema and all P9 tables created."""
    if static_pool:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS analytics")
    ANALYTICS_METADATA.create_all(engine)
    return engine


def analytics_session(engine: sa.Engine) -> Session:
    """A session bound to the analytics engine."""
    return sessionmaker(bind=engine)()


@contextmanager
def analytics_db_session() -> Generator[Session, None, None]:
    """Provide a transactional in-memory analytics SQLite session."""
    engine = create_analytics_engine(static_pool=True)
    session = analytics_session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()

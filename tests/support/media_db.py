"""SQLite `media` schema harness for unit tests."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from media_factory.storage.models import MEDIA_METADATA
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["create_media_engine", "media_db_session", "media_session"]


def create_media_engine(*, static_pool: bool = False) -> sa.Engine:
    """In-memory SQLite with an attached `media` schema and all P6 tables created."""
    if static_pool:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS media")
    MEDIA_METADATA.create_all(engine)
    return engine


def media_session(engine: sa.Engine) -> Session:
    """A session bound to the engine."""
    return sessionmaker(bind=engine)()


@contextmanager
def media_db_session() -> Generator[Session, None, None]:
    """Provide a transactional in-memory media SQLite session."""
    engine = create_media_engine(static_pool=True)
    session = media_session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()

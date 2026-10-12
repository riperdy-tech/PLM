"""SQLite `youtube` schema harness for unit tests."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
from youtube_publishing.storage.models import YOUTUBE_METADATA

__all__ = ["create_youtube_engine", "youtube_db_session", "youtube_session"]


def create_youtube_engine(*, static_pool: bool = False) -> sa.Engine:
    """In-memory SQLite with an attached `youtube` schema and all P7 tables created."""
    if static_pool:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS youtube")
    YOUTUBE_METADATA.create_all(engine)
    return engine


def youtube_session(engine: sa.Engine) -> Session:
    """A session bound to the engine."""
    return sessionmaker(bind=engine)()


@contextmanager
def youtube_db_session() -> Generator[Session, None, None]:
    """Provide a transactional in-memory youtube SQLite session."""
    engine = create_youtube_engine(static_pool=True)
    session = youtube_session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()

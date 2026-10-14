"""SQLite `music` schema harness for unit tests."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from plm_platform.exceptions import EXCEPTIONS_METADATA
from sqlalchemy.orm import Session, sessionmaker

from services.music_factory.storage.models import MUSIC_METADATA

__all__ = ["create_music_engine", "music_db_session", "music_session"]


def create_music_engine(*, static_pool: bool = False) -> sa.Engine:
    """In-memory SQLite with an attached `music` schema and all P4 tables created."""
    if static_pool:
        engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS music")
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ops")
    MUSIC_METADATA.create_all(engine)
    EXCEPTIONS_METADATA.create_all(engine)  # P4 raises P29 holds on refused prompts
    return engine


def music_session(engine: sa.Engine) -> Session:
    """A session bound to the engine."""
    return sessionmaker(bind=engine)()


@contextmanager
def music_db_session() -> Generator[Session, None, None]:
    """Provide a transactional in-memory music SQLite session."""
    engine = create_music_engine(static_pool=True)
    session = music_session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


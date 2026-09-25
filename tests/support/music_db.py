"""SQLite `music` schema harness for unit tests."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from services.music_factory.storage.models import MUSIC_METADATA

__all__ = ["create_music_engine", "music_session"]


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
    MUSIC_METADATA.create_all(engine)
    return engine


def music_session(engine: sa.Engine) -> Session:
    """A session bound to the engine."""
    return sessionmaker(bind=engine)()

# tests/unit/market/conftest.py
"""Fast unit-test database: SQLite with `market` attached, so the same Core
statements that run on Postgres run here. Integration tests still prove Postgres."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from services.market_intelligence.storage.models import MARKET_METADATA


@pytest.fixture
def market_db() -> sa.Engine:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS market")
    MARKET_METADATA.create_all(engine)
    return engine


@pytest.fixture
def market_session(market_db: sa.Engine) -> Iterator[Session]:
    with Session(market_db) as session:
        yield session

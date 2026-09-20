# tests/unit/market/conftest.py
"""Fast unit-test database: SQLite with `market` attached, so the same Core
statements that run on Postgres run here. Integration tests still prove Postgres."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from tests.support.market_db import create_market_engine


@pytest.fixture
def market_db() -> sa.Engine:
    return create_market_engine()


@pytest.fixture
def market_session(market_db: sa.Engine) -> Iterator[Session]:
    with Session(market_db) as session:
        yield session

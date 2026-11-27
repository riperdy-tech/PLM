"""Live check: what alembic created must equal what storage/models.py declares."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from market_intelligence.storage.models import MARKET_METADATA, MARKET_TABLES
from plm_platform.db import build_engine

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> Iterator[sa.Engine]:
    active = build_engine()
    try:
        with active.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy, repo convention
        pytest.skip(f"postgres unreachable ({type(exc).__name__}); run: docker compose up -d --wait")
    yield active
    active.dispose()


def test_tables_and_columns_match_the_models(engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    live_tables = set(inspector.get_table_names(schema="market"))
    assert set(MARKET_TABLES) <= live_tables, (
        "run `python -m plm_platform.cli migrate` first: the market tables are missing"
    )
    for key, table in MARKET_METADATA.tables.items():
        live_columns = {c["name"] for c in inspector.get_columns(table.name, schema="market")}
        assert {c.name for c in table.columns} == live_columns, f"{key}: columns drifted"

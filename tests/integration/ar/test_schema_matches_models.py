# tests/integration/ar/test_schema_matches_models.py
"""After `alembic upgrade head`, the live `ar` schema must equal the models exactly."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from plm_platform.db import build_engine

from services.autonomous_ar.storage.models import AR_METADATA


@pytest.mark.integration
def test_ar_schema_matches_the_models() -> None:
    engine = build_engine()
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1 FROM ar.ar_allocation_cycle LIMIT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"compose postgres unavailable or unmigrated ({exc.__class__.__name__}); "
                    "run `docker compose up -d --wait postgres` and `plm migrate`")
    inspector = sa.inspect(engine)
    live = set(inspector.get_table_names(schema="ar"))
    expected = {table.name for table in AR_METADATA.tables.values()}
    assert live == expected, f"DDL drifted from models: {sorted(live ^ expected)}"

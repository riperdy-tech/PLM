# tests/unit/ar/test_migration_module.py
"""The migration and the models must describe the same DDL (P1.2's drift-guard pattern)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from services.autonomous_ar.storage.models import AR_METADATA

MIGRATION_PATH = Path("migrations/versions/ar/ar_0008_initial_tables.py")


def _load_migration() -> object:
    spec = importlib.util.spec_from_file_location("ar_0008_initial_tables", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_metadata_chains_onto_the_market_head() -> None:
    module = _load_migration()
    assert module.revision == "0008_ar_initial"  # <= 32 chars (alembic_version is varchar(32))
    assert module.down_revision == "0007_market_snapshot_bodies"


def test_upgrade_creates_exactly_the_model_tables_and_downgrade_removes_them() -> None:
    module = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ar")
        module.op = Operations(MigrationContext.configure(connection))  # type: ignore[attr-defined]
        module.upgrade()  # type: ignore[attr-defined]
        created = set(sa.inspect(connection).get_table_names(schema="ar"))
        assert created == {table.name for table in AR_METADATA.tables.values()}
        module.downgrade()  # type: ignore[attr-defined]
        assert set(sa.inspect(connection).get_table_names(schema="ar")) == set()

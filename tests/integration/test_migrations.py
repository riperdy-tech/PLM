"""W0.5 integration: Alembic chain tests with a destructive target guard.

The round-trip test drops all nine phase schemas by downgrading to base.
It must NEVER run on the shared development database. Only an explicitly
selected disposable database is eligible, after verifying current_database().
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from plm_platform.config import Settings, load_settings
from plm_platform.db import (
    SCHEMAS,
    build_engine,
    database_url,
    scoped_session,
    session_factory,
)
from plm_platform.migration_safety import user_object_count
from sqlalchemy import text

from tests.support.migration_safety import (
    destructive_migration_name_refusal,
    destructive_migration_target_refusal,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _settings() -> Settings:
    return load_settings(dotenv_path=REPO_ROOT / ".env")


def _skip_reason() -> str | None:
    url = database_url(_settings())
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        return (
            f"postgres not reachable at {url.host}:{url.port} - {type(exc).__name__}: {exc}; "
            "start it with: docker compose up -d --wait postgres"
        )
    return None


@pytest.fixture(scope="module", autouse=True)
def _require_postgres() -> None:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def _schema_names() -> set[str]:
    engine = build_engine(_settings())
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT schema_name FROM information_schema.schemata"))
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _require_disposable_migration_database() -> None:
    """Refuse destructive Alembic calls on the default/shared database."""
    settings = _settings()
    configured = database_url(settings).database
    declared = os.environ.get("PLM_DESTRUCTIVE_MIGRATION_TEST_DB")
    # The opt-in must be valid before opening any database connection.
    initial_refusal = destructive_migration_name_refusal(configured, declared)
    if initial_refusal is not None:
        pytest.skip(f"destructive migration test disabled: {initial_refusal}")
    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            connected = connection.execute(text("SELECT current_database()")).scalar_one()
            # A disposable target must really be empty, including unrelated
            # user schemas and public routines/types/extensions. Use exactly
            # the same read-only catalog inventory as 'plm migrate'.
            existing_objects = user_object_count(connection)
    finally:
        engine.dispose()
    reason = destructive_migration_target_refusal(
        configured, declared, connected, existing_objects
    )
    if reason is not None:
        pytest.skip(f"destructive migration test disabled: {reason}")


def test_upgrade_downgrade_upgrade_round_trips_the_nine_schemas() -> None:
    _require_disposable_migration_database()
    config = _alembic_config()
    command.upgrade(config, "head")
    assert set(SCHEMAS) <= _schema_names()
    command.downgrade(config, "base")
    assert not set(SCHEMAS) & _schema_names()
    command.upgrade(config, "head")
    assert set(SCHEMAS) <= _schema_names()


def test_scoped_session_points_unqualified_queries_at_the_phase_schema() -> None:
    maker = session_factory(build_engine(_settings()))
    for schema in ("market", "analytics"):
        with scoped_session(schema, factory=maker) as session:
            assert session.execute(text("SELECT current_schema()")).scalar_one() == schema

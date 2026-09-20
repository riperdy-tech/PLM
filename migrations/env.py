"""Alembic environment for the single PLM migration chain (W0.5).

The connection string is built from :mod:`plm_platform.config` — this file never
touches the process environment directly and never hardcodes a URL. ``.env`` is
resolved at the repository root, so alembic behaves the same from any working
directory.

Offline (``alembic upgrade head --sql``) and online modes are both supported;
online runs use a NullPool because a migration run is one-shot and must not
lingering pooled connections. There is no ORM metadata to autogenerate from:
revisions in ``migrations/versions/<schema>/`` are hand-written.
"""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from plm_platform.config import Settings, load_settings
from plm_platform.db import database_url
from sqlalchemy import create_engine, pool

_REPO_ROOT = Path(__file__).resolve().parents[1]

config = context.config

# Standard alembic wiring: apply the [loggers] sections of alembic.ini.
# disable_existing_loggers=False so an in-process alembic run (tests, tooling)
# never disables application loggers such as plm_platform.db.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


def _settings() -> Settings:
    """Settings with ``.env`` pinned to the repository root (CWD-independent)."""
    return load_settings(dotenv_path=_REPO_ROOT / ".env")


def _migration_url() -> str:
    """The full connection string for this run; rendered only at connect time."""
    return database_url(_settings()).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without touching a database."""
    context.configure(
        url=_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the chain against the configured database."""
    connectable = create_engine(_migration_url(), poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

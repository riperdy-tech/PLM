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
from alembic.script import ScriptDirectory
from plm_platform.config import Settings, load_settings
from plm_platform.db import database_url
from plm_platform.migration_safety import code_only_0039_alembic_refusal
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import Connection

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
    """Run migrations only if the connected target passes the code-only hold.

    This guard also covers direct `alembic` invocations, which bypass
    `plm migrate`. It does not confer permission to perform any migration.
    """
    settings = _settings()
    url = database_url(settings)
    known = frozenset(
        revision.revision
        for revision in ScriptDirectory.from_config(config).walk_revisions()
    )

    # One separately approved local-development rollout can lend an already
    # inspected Connection. Normal CLI and raw Alembic never set this attribute.
    # This does NOT enable a general shared/production database bypass.
    approved = config.attributes.get("plm_approved_0039_local_connection")
    if approved is not None:
        if (
            type(approved) is not Connection
            or settings.env != "local"
            or url.database != "plm"
            or url.host not in ("127.0.0.1", "localhost")
            or url.port != 5432
            or approved.engine.url != url
            or "0039_merge_0038_branches" not in known
            or approved.execute(text("SELECT current_database()")).scalar_one() != "plm"
            or approved.execute(text(
                "SELECT version_num FROM public.alembic_version"
            )).scalars().all() != ["0038_release_reviews"]
            or int(approved.execute(text(
                "SELECT current_setting('server_version_num')"
            )).scalar_one()) // 10000 != 16
        ):
            raise RuntimeError("approved local 0039 migration connection identity refused")
        context.configure(connection=approved, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    # First refuse known shared/unsafe URLs BEFORE connecting.
    reason = code_only_0039_alembic_refusal(
        known_revisions=known,
        configured_database=url.database,
        connected_database=url.database,
        host=url.host,
        port=url.port,
    )
    if reason is not None:
        raise RuntimeError(reason)

    connectable = create_engine(
        url.render_as_string(hide_password=False), poolclass=pool.NullPool
    )
    try:
        with connectable.connect() as connection:
            # A connection target can differ from its configured database name;
            # verify the actual PostgreSQL identity before migration SQL.
            actual_name = connection.execute(text("SELECT current_database()")).scalar_one()
            connection.rollback()  # End the inspection's implicit transaction.
            reason = code_only_0039_alembic_refusal(
                known_revisions=known,
                configured_database=url.database,
                connected_database=actual_name,
                host=url.host,
                port=url.port,
            )
            if reason is not None:
                raise RuntimeError(reason)
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

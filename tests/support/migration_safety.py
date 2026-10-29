"""Pure target guard for the deliberately destructive migration integration test.

This is not authorization to run a migration on shared data. The integration
test separately checks current_database() before invoking Alembic.
"""
from __future__ import annotations

from plm_platform.migration_safety import DISPOSABLE_DATABASE_NAME


def destructive_migration_name_refusal(
    configured_database: str | None,
    opted_in_database: str | None,
) -> str | None:
    """Check explicit opt-in before opening a connection to the database."""
    if (
        configured_database is None
        or DISPOSABLE_DATABASE_NAME.fullmatch(configured_database) is None
    ):
        return (
            "a separately named disposable database "
            "(plm_migration_disposable_* or plm_r5_*) is required"
        )
    if opted_in_database != configured_database:
        return "explicit opt-in must name the exact disposable database"
    return None


def destructive_migration_target_refusal(
    configured_database: str | None,
    opted_in_database: str | None,
    connected_database: str | None,
    existing_objects: int | None,
) -> str | None:
    """Refuse a wrong or nonempty database before any destructive migration."""
    name_reason = destructive_migration_name_refusal(configured_database, opted_in_database)
    if name_reason is not None:
        return name_reason
    if connected_database != configured_database:
        return "connected PostgreSQL database identity differs from the intended target"
    if existing_objects is None or existing_objects < 0:
        return "could not establish whether the disposable database is empty"
    if existing_objects:
        return "disposable database already contains user objects; refusing destructive test"
    return None

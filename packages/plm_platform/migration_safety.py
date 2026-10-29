"""Read-only PostgreSQL catalog inventory shared by migration entry points.

No operation in this module changes a database. A zero count is a necessary
precondition for starting a fresh migration, not authorisation to migrate.
"""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

_USER_OBJECT_COUNT_SQL = text("""
    SELECT (
        SELECT count(*) FROM pg_catalog.pg_namespace
        WHERE nspname NOT IN ('public', 'information_schema')
          AND left(nspname, 3) <> 'pg_'
    ) + (
        SELECT count(*) FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'f', 'v', 'm', 'S')
    ) + (
        SELECT count(*) FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
    ) + (
        SELECT count(*) FROM pg_catalog.pg_type t
        JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
          AND t.typtype IN ('c', 'd', 'e', 'm', 'r')
    ) + (
        SELECT count(*) FROM pg_catalog.pg_extension e
        JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
        WHERE n.nspname = 'public'
    )
""")


def user_object_count(connection: Connection) -> int:
    """Count existing user objects without reading their contents.

    Fail closed if the catalog returns a missing, negative or non-integer
    count. Callers separately verify target DB identity and authorisation.
    """
    count = connection.execute(_USER_OBJECT_COUNT_SQL).scalar_one()
    if type(count) is not int or count < 0:
        raise RuntimeError("cannot verify whether the unversioned database contains user objects")
    return count


# Names reserved for independently provisioned throwaway databases: Alembic
# rehearsals (plm_migration_disposable_*) and the Gate R5 stack (plm_r5_*).
DISPOSABLE_DATABASE_NAME = re.compile(r"plm_(?:migration_disposable|r5)_[a-z0-9_]+")


def is_disposable_migration_target(
    *,
    configured_database: object,
    connected_database: object,
    host: object,
    port: object,
) -> bool:
    """The one target rule shared by `plm migrate`, raw Alembic and Gate R5.

    A reserved disposable name, identical configured and connected names, a
    loopback host and a non-default port. Checking the actual connected name
    prevents a DSN/name mismatch. This is a guard, not proof that a database
    is isolated or disposable.
    """
    return (
        type(configured_database) is str
        and type(connected_database) is str
        and configured_database == connected_database
        and DISPOSABLE_DATABASE_NAME.fullmatch(connected_database) is not None
        and type(host) is str
        and host in ("127.0.0.1", "localhost")
        and type(port) is int
        and 1024 <= port <= 65535
        and port != 5432
    )


def code_only_0039_alembic_refusal(
    *,
    known_revisions: frozenset[str],
    configured_database: object,
    connected_database: object,
    host: object,
    port: object,
) -> str | None:
    """Block direct Alembic DB writes after 0039 code-only installation.

    Alembic bypasses the guarded `plm migrate` CLI. Until a separately
    approved rollout changes this policy, only independently provisioned
    disposable databases on a non-default loopback port can be migrated.
    """
    if "0039_merge_0038_branches" not in known_revisions:
        return None

    if is_disposable_migration_target(
        configured_database=configured_database,
        connected_database=connected_database,
        host=host,
        port=port,
    ):
        return None
    return (
        "0039 is installed in code ONLY; direct Alembic database operations "
        "are blocked for non-disposable targets until a separate rollout approval"
    )

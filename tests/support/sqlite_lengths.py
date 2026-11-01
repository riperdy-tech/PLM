"""Make SQLite refuse strings longer than their VARCHAR, as PostgreSQL does.

SQLite ignores ``VARCHAR(n)``, so a value that PostgreSQL refuses with
``StringDataRightTruncation`` passes every SQLite unit test. The first real Flow Music
import (2026-11-01) failed that way on a 190-character rights basis in a 128 column.
:func:`enforce_varchar_lengths` checks every INSERT and UPDATE that SQLAlchemy compiles
against a table and raises :class:`VarcharOverflow` before the statement runs.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.sql.dml import Insert, Update

__all__ = ["VarcharOverflow", "enforce_varchar_lengths"]


class VarcharOverflow(sa.exc.DataError):
    """The value would not fit its column on PostgreSQL."""

    def __init__(self, message: str, statement: str) -> None:
        super().__init__(statement, None, ValueError(message))


def _limits(table: sa.Table) -> dict[str, int]:
    return {c.key: c.type.length for c in table.columns
            if isinstance(c.type, sa.String) and c.type.length is not None}


def _check(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any,
           executemany: bool) -> None:
    if conn.dialect.name != "sqlite" or context is None:
        return
    compiled = getattr(context, "compiled", None)
    stmt = getattr(compiled, "statement", None)
    if not isinstance(stmt, Insert | Update) or not isinstance(stmt.table, sa.Table):
        return
    limits = _limits(stmt.table)
    if not limits:
        return
    for params in context.compiled_parameters or ():
        for name, value in params.items():
            limit = limits.get(name)
            if limit is not None and isinstance(value, str) and len(value) > limit:
                raise VarcharOverflow(
                    f"{stmt.table.fullname}.{name}: {len(value)} characters do not fit "
                    f"VARCHAR({limit}) (PostgreSQL would refuse this value)", statement)


def enforce_varchar_lengths() -> None:
    """Install the check on every engine (idempotent)."""
    if not sa.event.contains(Engine, "before_cursor_execute", _check):
        sa.event.listen(Engine, "before_cursor_execute", _check)

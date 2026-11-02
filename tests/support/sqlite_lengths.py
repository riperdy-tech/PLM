"""Make SQLite refuse strings longer than their VARCHAR, as PostgreSQL does.

SQLite ignores ``VARCHAR(n)``, so a value that PostgreSQL refuses with
``StringDataRightTruncation`` passes every SQLite unit test. The first real Flow Music
import (2026-11-01) failed that way on a 190-character rights basis in a 128 column.
:func:`enforce_varchar_lengths` checks every INSERT and UPDATE that SQLAlchemy compiles
against a table and raises :class:`VarcharOverflow` before the statement runs.

It reads the parameters as they go to the driver, after each type's bind processing (so
a ``TypeDecorator`` over a ``String`` is checked on the text it stores), and matches
them to their bind parameters by position (so multi-row ``values([...])`` and custom
``bindparam`` values are checked too). A WHERE clause's parameters are not checked:
PostgreSQL compares a longer string without refusing it.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.sql import visitors
from sqlalchemy.sql.dml import Insert, Update

__all__ = ["VarcharOverflow", "enforce_varchar_lengths"]


class VarcharOverflow(sa.exc.DataError):
    """The value would not fit its column on PostgreSQL."""

    def __init__(self, message: str, statement: str) -> None:
        super().__init__(statement, None, ValueError(message))


def _length(type_: Any) -> int | None:
    impl = getattr(type_, "impl_instance", type_)  # a TypeDecorator stores its impl's type
    return impl.length if isinstance(impl, sa.String) else None


def _check(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any,
           executemany: bool) -> None:
    if conn.dialect.name != "sqlite" or context is None:
        return
    compiled: Any = getattr(context, "compiled", None)
    stmt = getattr(compiled, "statement", None)
    if not isinstance(stmt, Insert | Update) or not isinstance(stmt.table, sa.Table):
        return
    names = compiled.positiontup or []
    where = getattr(stmt, "whereclause", None)
    compared = set() if where is None else {
        id(compiled.binds[b.key]) for b in visitors.iterate(where)
        if isinstance(b, sa.BindParameter) and b.key in compiled.binds
    }
    limits = {
        i: (name, limit) for i, name in enumerate(names)
        if id(compiled.binds[name]) not in compared
        and (limit := _length(compiled.binds[name].type)) is not None
    }
    if not limits:
        return
    # An "insertmanyvalues" batch sends several rows' parameters as one flat row (and is
    # flagged executemany), so the rows are told apart by shape.
    width = len(names)
    many = bool(parameters) and isinstance(parameters[0], list | tuple)
    for row in parameters if many else [parameters]:
        for start in range(0, len(row) - width + 1, width):
            for i, (name, limit) in limits.items():
                value = row[start + i]
                if isinstance(value, str) and len(value) > limit:
                    raise VarcharOverflow(
                        f"{stmt.table.fullname}.{name}: {len(value)} characters do not "
                        f"fit VARCHAR({limit}) (PostgreSQL would refuse this value)", statement)


def enforce_varchar_lengths() -> None:
    """Install the check on every engine (idempotent)."""
    if not sa.event.contains(Engine, "before_cursor_execute", _check):
        sa.event.listen(Engine, "before_cursor_execute", _check)

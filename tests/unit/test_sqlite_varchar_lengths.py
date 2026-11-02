"""The SQLite test engines refuse strings their PostgreSQL column would refuse."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa

from tests.support.sqlite_lengths import VarcharOverflow


class _Tripled(sa.types.TypeDecorator[str]):
    """Stores three copies of its value: only the stored text shows the overflow."""

    impl = sa.String(6)
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        return None if value is None else value * 3


METADATA = sa.MetaData()
NOTES = sa.Table("notes", METADATA, sa.Column("id", sa.Integer, primary_key=True),
                 sa.Column("body", sa.String(8)), sa.Column("free", sa.Text),
                 sa.Column("tripled", _Tripled()))


def test_an_overlong_insert_or_update_is_refused_and_a_fitting_one_passes() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    METADATA.create_all(engine)
    with engine.begin() as conn:
        conn.execute(NOTES.insert().values(id=1, body="12345678", free="x" * 500))
        with pytest.raises(VarcharOverflow, match=r"notes\.body: 9 characters"):
            conn.execute(NOTES.insert().values(id=2, body="123456789"))
        with pytest.raises(VarcharOverflow):
            conn.execute(NOTES.update().where(NOTES.c.id == 1).values(body="x" * 9))
        with pytest.raises(VarcharOverflow):
            conn.execute(NOTES.insert(), [{"id": 3, "body": "ok"}, {"id": 4, "body": "x" * 9}])


def test_every_way_of_passing_a_value_is_checked_but_a_where_clause_is_not() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    METADATA.create_all(engine)
    with engine.begin() as conn:
        with pytest.raises(VarcharOverflow, match=r"notes\.body_m1: 9 characters"):
            conn.execute(NOTES.insert().values([{"id": 1, "body": "ok"},
                                                {"id": 2, "body": "x" * 9}]))
        with pytest.raises(VarcharOverflow):
            conn.execute(NOTES.insert().values(id=3, body=sa.bindparam("custom")),
                         {"custom": "x" * 9})
        with pytest.raises(VarcharOverflow, match=r"notes\.tripled: 9 characters"):
            conn.execute(NOTES.insert().values(id=4, tripled="abc"))
        conn.execute(NOTES.insert().values(id=5, body="ok", tripled="ab"))
        conn.execute(NOTES.update().where(NOTES.c.body == "x" * 20).values(body="fits"))
        with pytest.raises(VarcharOverflow):  # an insertmanyvalues batch, rows sent flat
            conn.execute(NOTES.insert().returning(NOTES.c.id),
                         [{"id": 6, "body": "ok"}, {"id": 7, "body": "x" * 9}])

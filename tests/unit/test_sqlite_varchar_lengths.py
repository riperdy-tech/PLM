"""The SQLite test engines refuse strings their PostgreSQL column would refuse."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from tests.support.sqlite_lengths import VarcharOverflow

METADATA = sa.MetaData()
NOTES = sa.Table("notes", METADATA, sa.Column("id", sa.Integer, primary_key=True),
                 sa.Column("body", sa.String(8)), sa.Column("free", sa.Text))


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

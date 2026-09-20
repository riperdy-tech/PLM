"""A throwaway ``market`` database for storage tests.

The unit suite runs the same Core statements on SQLite (with a second in-memory
database attached as the ``market`` schema) that the integration suite runs on
Postgres. Tests that need *independently created* databases — e.g. resolving one
corpus into two fresh instances and comparing the ids it minted — call
:func:`create_market_engine` once per instance.
"""

from __future__ import annotations

import sqlalchemy as sa

from services.market_intelligence.storage.models import MARKET_METADATA

__all__ = ["create_market_engine"]


def create_market_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS market")
    MARKET_METADATA.create_all(engine)
    return engine

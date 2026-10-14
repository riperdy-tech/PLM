"""Drift guard: migration ``0030_ops_pipeline_runs`` creates exactly the tables the store
declares, and PostgreSQL refuses to rewrite the transition history.

Proves the migrated compose database, not SQLite: the unit tier builds its tables from
the store's metadata, so only this test can catch the two diverging.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from plm_platform.pipeline.store import PIPELINE_METADATA, PipelineStore, pipeline_transitions
from plm_platform.pipeline.types import TransitionRecord
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def test_the_migrated_pipeline_tables_match_the_store(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        inspector = sa.inspect(session.get_bind())
        for table in PIPELINE_METADATA.tables.values():
            columns = {c["name"]: c for c in inspector.get_columns(table.name, schema="ops")}
            assert set(columns) == {c.name for c in table.columns}, table.name
            for column in table.columns:
                assert columns[column.name]["nullable"] == column.nullable, (
                    f"{table.name}.{column.name} nullability drifted")
        uniques = inspector.get_unique_constraints("pipeline_transitions", schema="ops")
        assert {"workflow_id", "sequence"} in [set(u["column_names"]) for u in uniques]


def test_the_transition_history_is_append_only(factory: sessionmaker[Session]) -> None:
    workflow_id = f"pipeline.gate-schema-{uuid.uuid4().hex[:12]}"
    with factory() as session:
        PipelineStore().record(session, TransitionRecord(
            workflow_id=workflow_id, sequence=1, from_state=None, to_state="IDEA",
            occurred_at=datetime.now(UTC), directive_id="gate-schema", brand_id="gate_brand"))
        session.commit()
    mine = pipeline_transitions.c.workflow_id == workflow_id
    with factory() as session, pytest.raises(DBAPIError, match="append-only"):
        session.execute(pipeline_transitions.update().where(mine).values(error_code="x"))
    with factory() as session, pytest.raises(DBAPIError, match="append-only"):
        session.execute(pipeline_transitions.delete().where(mine))

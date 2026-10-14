"""Pipeline persistence (SQLite ``ops``): idempotent per (workflow_id, sequence), and only
lifecycles the P27 rules allow.

Proves the store's rules in-process. The same rules against PostgreSQL, written by a
running workflow, are proved by the Gate R3 integration tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from plm_platform.pipeline.store import (
    PIPELINE_METADATA,
    PipelineStore,
    TransitionConflictError,
    TransitionRejectedError,
)
from plm_platform.pipeline.types import MoneyAmount, TransitionRecord
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
WF = "pipeline.dir-1"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", poolclass=sa.pool.StaticPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ops")
    PIPELINE_METADATA.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    engine.dispose()


def _rec(sequence: int, from_state: str | None, to_state: str, **fields: object) -> TransitionRecord:
    return TransitionRecord(
        workflow_id=WF, sequence=sequence, from_state=from_state, to_state=to_state,
        occurred_at=T0 + timedelta(seconds=sequence), directive_id="dir-1", brand_id="brand",
        **fields,  # type: ignore[arg-type]
    )


def _walk(store: PipelineStore, session: Session, *states: str) -> int:
    previous: str | None = None
    for n, state in enumerate(states, start=1):
        store.record(session, _rec(n, previous, state))
        previous = state
    return len(states)


def test_a_replayed_identical_transition_is_a_no_op(session: Session) -> None:
    store = PipelineStore()
    first = store.record(session, _rec(1, None, "IDEA"))
    again = store.record(session, _rec(1, None, "IDEA"))
    assert (first.created, again.created) == (True, False)
    assert len(store.transitions(session, WF)) == 1
    run = store.run(session, WF)
    assert run is not None and run.state.value == "IDEA" and run.last_sequence == 1


def test_a_different_event_under_a_recorded_sequence_is_refused(session: Session) -> None:
    store = PipelineStore()
    store.record(session, _rec(1, None, "IDEA"))
    with pytest.raises(TransitionConflictError):
        store.record(session, _rec(1, None, "IDEA", error_code="something_else"))


def test_sequences_must_be_contiguous_and_follow_the_run(session: Session) -> None:
    store = PipelineStore()
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(2, None, "IDEA"))  # no run yet
    store.record(session, _rec(1, None, "IDEA"))
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(3, "IDEA", "RESEARCHED"))  # gap
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(2, "RESEARCHED", "POLICY_CHECKED"))  # wrong origin


def test_illegal_transitions_are_refused(session: Session) -> None:
    store = PipelineStore()
    store.record(session, _rec(1, None, "IDEA"))
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(2, "IDEA", "GENERATING"))
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(2, "IDEA", "NOT_A_STATE"))


def test_every_transition_keeps_its_facts(session: Session) -> None:
    store = PipelineStore()
    n = _walk(store, session, "IDEA", "RESEARCHED", "POLICY_CHECKED", "GENERATING")
    record = _rec(
        n + 1, "GENERATING", "GENERATED", step="generate", idempotency_key=f"{WF}:generate",
        attempt=3, started_at=T0, provider_ids={"plan_id": "plan-1"},
        cost=MoneyAmount(amount="2.50", currency="USD"), cost_basis="provider_reported",
        inputs={"package_ids": ["p1"]}, outputs={"candidate_ids": ["c1"]},
    )
    store.record(session, record)
    stored = store.transitions(session, WF)[-1]
    assert stored.attempt == 3 and stored.retry_count == 2
    assert stored.cost == MoneyAmount(amount="2.500000", currency="USD")
    assert stored.cost_basis == "provider_reported"
    assert stored.provider_ids == {"plan_id": "plan-1"}
    assert stored.started_at == T0
    assert stored.inputs == {"package_ids": ["p1"]}
    assert stored.outputs == {"candidate_ids": ["c1"]}
    # A replay of the same event with the same exact amount spelled differently is the
    # same fact: the digest normalises the decimal.
    assert store.record(session, replace(
        record, cost=MoneyAmount(amount="2.5", currency="USD"))).created is False


def test_money_needs_an_iso_currency_and_a_decimal(session: Session) -> None:
    store = PipelineStore()
    store.record(session, _rec(1, None, "IDEA"))
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(2, "IDEA", "RESEARCHED",
                                   cost=MoneyAmount(amount="1.00", currency="")))
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(2, "IDEA", "RESEARCHED",
                                   cost=MoneyAmount(amount="one", currency="USD")))


def test_held_records_where_it_returns_and_returns_only_there(session: Session) -> None:
    store = PipelineStore()
    n = _walk(store, session, "IDEA", "RESEARCHED", "POLICY_CHECKED", "GENERATING",
              "GENERATED", "QA")
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(n + 1, "QA", "HELD"))  # held_from missing
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(n + 1, "QA", "HELD", held_from="GENERATED"))  # wrong
    store.record(session, _rec(n + 1, "QA", "HELD", held_from="QA", hold_case_ids=("exc_1",),
                               error_code="qa_quarantined"))
    store.record(session, _rec(n + 2, "HELD", "HELD", held_from="QA",
                               error_code="reviewer_not_authorized"))
    run = store.run(session, WF)
    assert run is not None and run.state.value == "HELD" and run.held_from is not None
    assert run.held_from.value == "QA" and run.last_error_code == "reviewer_not_authorized"
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(n + 3, "HELD", "PASSED"))  # a hold never skips QA
    store.record(session, _rec(n + 3, "HELD", "QA"))
    run = store.run(session, WF)
    assert run is not None and run.state.value == "QA" and run.held_from is None


def test_a_final_transition_completes_the_run_and_terminal_states_are_closed(
    session: Session,
) -> None:
    store = PipelineStore()
    store.record(session, _rec(1, None, "IDEA"))
    store.record(session, _rec(2, "IDEA", "FAILED", step="research", error_code="x",
                               final=True))
    run = store.run(session, WF)
    assert run is not None and run.completed_at == T0 + timedelta(seconds=2)
    with pytest.raises(TransitionRejectedError):
        store.record(session, _rec(3, "FAILED", "RESEARCHED"))

"""PART XXI decision log: recorded once, results attached once per window."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from plm_platform.decision_log import (
    DECISION_LOG_METADATA,
    DecisionConflictError,
    DecisionNotFoundError,
    DecisionRecord,
    load_decision,
    record_decision,
    record_result,
)
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def session() -> Session:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ops")
    DECISION_LOG_METADATA.create_all(engine)
    return sessionmaker(bind=engine)()


def _record(**overrides: object) -> DecisionRecord:
    fields: dict[str, object] = {
        "decision_key": "allocation:dir-1", "decision_kind": "allocation",
        "origin_phase": "ar", "subject_ref": "arm-1", "decided_at": T0,
        "decision": "exploit allocation to arm-1", "action": {"quantity": 1},
        "input_data": {"posterior_mean": 0.6}, "model_version": "p2-nn-v1",
        "policy_version": "p2-v1", "confidence": Decimal("0.81"),
    }
    fields.update(overrides)
    return DecisionRecord(**fields)  # type: ignore[arg-type]


def test_a_decision_is_recorded_once_and_a_different_replay_is_refused(session: Session) -> None:
    record_decision(session, _record())
    record_decision(session, _record())
    with pytest.raises(DecisionConflictError):
        record_decision(session, _record(action={"quantity": 2}))
    stored = load_decision(session, "allocation:dir-1")
    assert stored is not None and stored.confidence == Decimal("0.81")
    assert stored.decided_at == T0


def test_results_attach_once_per_window_and_unit(session: Session) -> None:
    record_decision(session, _record())
    seven = {"maturity": "mature", "normalized_reward": "0.72",
             "net_contribution": {"amount": "95.000000", "currency": "KRW"}}
    record_result(session, "allocation:dir-1", window="7d", unit_id="vid_1", result=seven, recorded_at=T0)
    record_result(session, "allocation:dir-1", window="7d", unit_id="vid_1", result=seven, recorded_at=T0)
    record_result(session, "allocation:dir-1", window="28d", unit_id="vid_1",
                  result={**seven, "normalized_reward": "0.70"}, recorded_at=T0)
    # A second unit of the same decision (another treatment's video) is its own result.
    record_result(session, "allocation:dir-1", window="28d", unit_id="vid_2",
                  result={**seven, "normalized_reward": "0.40"}, recorded_at=T0)
    with pytest.raises(DecisionConflictError):
        record_result(session, "allocation:dir-1", window="7d", unit_id="vid_1",
                      result={**seven, "normalized_reward": "0.10"}, recorded_at=T0)
    stored = load_decision(session, "allocation:dir-1")
    assert stored is not None and set(stored.result or {}) == {"7d", "28d"}
    assert set((stored.result or {})["28d"]) == {"vid_1", "vid_2"}


def test_a_result_for_an_unknown_decision_is_refused(session: Session) -> None:
    with pytest.raises(DecisionNotFoundError):
        record_result(session, "allocation:missing", window="28d", unit_id="vid_1", result={},
                      recorded_at=T0)


def test_a_naive_decision_time_is_refused(session: Session) -> None:
    with pytest.raises(ValueError):
        record_decision(session, _record(decided_at=datetime(2026, 10, 1)))

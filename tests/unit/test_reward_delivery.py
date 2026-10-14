"""C7 leg 2 through P9's transactional outbox into P2 (stage R3).

What these prove, and what they do not:

* In-process only. P9 runs over an in-memory SQLite ``analytics`` schema; P2 is its real
  FastAPI app (`autonomous_ar.api.app.create_app`) over an in-memory ``ar`` schema, reached
  through Starlette's `TestClient`, which is an `httpx.Client`, so P9's real
  `HttpTransport`/`HttpReconciler` speak to P2's real routes. No socket, no deployed
  service, no Postgres row locking.
* Where a scenario needs a lost response or a crash, a thin wrapper around the real
  transport injects it; the request itself still reaches P2's real route.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from analytics.api.app import create_app as create_analytics_app
from analytics.config import AnalyticsSettings
from analytics.domain.enums import ObservationWindow
from analytics.domain.models import NormalizedRewardPayload
from analytics.evaluation.delivery import (
    AR_RESULTS_PATH,
    REWARD_TOPIC,
    dispatch_rewards,
    p2_acknowledges,
    p2_lookup_present,
    p2_lookup_query,
    reward_delivery_status,
    reward_outbox,
)
from analytics.evaluation.feedback import evaluate_experiment_outcome, record_and_enqueue_reward
from analytics.storage.models import OUTBOX_EVENTS, ExperimentOutcomeRow, RewardPostedRow
from fastapi.testclient import TestClient
from plm_contracts.money import FxTable, Money
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.outbox import (
    Dispatcher,
    DispatchReport,
    HttpReconciler,
    HttpTransport,
    OutboxConflictError,
    OutboxMessage,
    OutboxStatus,
    SendOutcome,
    SendResult,
    Transport,
)
from sqlalchemy.orm import Session, sessionmaker

from services.autonomous_ar.api.app import create_app as create_ar_app
from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.storage import repositories as ar_repositories
from services.autonomous_ar.storage.models import ar_reward_observation
from tests.support.analytics_db import create_analytics_engine
from tests.support.ar_db import create_ar_engine
from tests.support.decisions import log_allocation_decision
from tests.support.seams import lineage_fields, sample_lineage

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TOKEN = "reward-seam-token"
ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9", "PLM_SERVICE_TOKEN": TOKEN,
}
LINEAGE = sample_lineage()


@dataclass
class Loop:
    """P9's database, P2's real app and database, and one shared fixed clock."""

    p9: Callable[[], Session]
    p2: Callable[[], Session]
    p2_client: TestClient
    clock: FixedClock

    def p2_rows(self) -> int:
        with self.p2() as session:
            return int(session.execute(
                sa.select(sa.func.count()).select_from(ar_reward_observation)).scalar_one())

    def message(self) -> OutboxMessage:
        with self.p9() as session:
            messages = reward_outbox.messages(session, topic=REWARD_TOPIC)
        assert len(messages) == 1
        return messages[0]


@pytest.fixture
def loop() -> Iterator[Loop]:
    p9_engine = create_analytics_engine(static_pool=True)
    log_allocation_decision(p9_engine, directive_id=LINEAGE.production_directive_id,
                            arm_id=LINEAGE.content_arm_id, decided_at=NOW)
    p2_engine = create_ar_engine(static_pool=True)
    p2_factory: Callable[[], Session] = sessionmaker(bind=p2_engine)
    with p2_factory() as session:  # P2 only accepts results for arms it knows (§72)
        ar_repositories.upsert_arm_state(session, brand_id="neon_seoul",
                                         arm_id=LINEAGE.content_arm_id, now=NOW)
        session.commit()
    settings = load_ar_settings(platform=load_settings(env=ENV))
    with TestClient(create_ar_app(session_factory=p2_factory, settings=settings)) as client:
        yield Loop(p9=sessionmaker(bind=p9_engine), p2=p2_factory, p2_client=client,
                   clock=FixedClock(NOW))
    p9_engine.dispose()
    p2_engine.dispose()


def _record(session: Session, *, views: int = 12000, unit_id: str = "vid_loop_01",
            arm_id: str | None = None) -> NormalizedRewardPayload:
    lineage = LINEAGE if arm_id is None else sample_lineage(content_arm_id=arm_id)
    outcome = evaluate_experiment_outcome(
        session, lineage=lineage, unit_id=unit_id, net_contribution=Money.of("85", "USD"),
        reporting_currency="USD", fx=FxTable(), views=views, watch_time_minutes=350.0,
        window=ObservationWindow.D7, published_at=NOW - timedelta(days=8),
        attribution_confidence=0.9, quality_status=None, observed_at=NOW,
    )
    return record_and_enqueue_reward(
        session, outcome, views=views, watch_time_minutes=350.0,
        average_percentage_viewed=None, subscribers_gained=None, estimated_revenue=None,
        now=NOW, enqueued_at=NOW,
    )


def _enqueue(loop: Loop, **kwargs: Any) -> NormalizedRewardPayload:
    with loop.p9() as session:
        payload = _record(session, **kwargs)
        session.commit()
    return payload


def _dispatch(loop: Loop, transport: Transport | None = None) -> DispatchReport:
    """One pass with the production wiring, or with a fault-injecting send wrapper and
    the real P2 reconciler."""
    if transport is None:
        return dispatch_rewards(loop.p9, client=loop.p2_client, token=TOKEN, clock=loop.clock)
    reconciler = HttpReconciler(loop.p2_client, token=TOKEN, query=p2_lookup_query,
                                present=p2_lookup_present)
    return Dispatcher(reward_outbox, transport=transport, reconciler=reconciler,
                      clock=loop.clock, topics=(REWARD_TOPIC,)).run_once(loop.p9)


class LostResponse:
    """Forwards to P2 (the fact lands) or not, then reports a timeout either way."""

    def __init__(self, inner: HttpTransport, *, forward: bool) -> None:
        self.inner, self.forward, self.sent = inner, forward, 0

    def send(self, message: OutboxMessage) -> SendResult:
        self.sent += 1
        if self.forward:
            self.inner.send(message)
        return SendResult(SendOutcome.AMBIGUOUS, code="ReadTimeout", detail="response lost")


class Crash(BaseException):
    """Simulates the dispatcher process dying; not an `Exception`, so nothing catches it."""


class CrashAfterSend:
    def __init__(self, inner: HttpTransport) -> None:
        self.inner = inner

    def send(self, message: OutboxMessage) -> SendResult:
        self.inner.send(message)
        raise Crash


def _real_transport(loop: Loop) -> HttpTransport:
    return HttpTransport(loop.p2_client, token=TOKEN, acknowledges=p2_acknowledges)


# --- the producer's transaction --------------------------------------------------------------


def test_reward_and_outbox_event_commit_atomically(loop: Loop) -> None:
    """Rollback ⇒ neither the outcome, the reward record nor the event exists; commit ⇒
    all three, the event PENDING, and the reward not yet counted as posted."""
    with loop.p9() as session:
        _record(session)
        session.rollback()
    with loop.p9() as session:
        for model in (ExperimentOutcomeRow, RewardPostedRow):
            assert session.execute(sa.select(model)).first() is None
        assert reward_outbox.messages(session) == []

    payload = _enqueue(loop)
    with loop.p9() as session:
        assert session.execute(sa.select(RewardPostedRow)).scalar_one() is not None
        assert reward_delivery_status(session, payload) is OutboxStatus.PENDING
    message = loop.message()
    assert message.destination == AR_RESULTS_PATH
    assert message.payload == payload.to_wire()
    assert loop.p2_rows() == 0  # nothing was sent in-request


def test_a_different_reward_for_a_queued_fact_is_refused_not_merged(loop: Loop) -> None:
    _enqueue(loop)
    _enqueue(loop)  # the identical reward again: idempotent
    with loop.p9() as session, pytest.raises(OutboxConflictError):
        _record(session, views=99999)
    assert loop.message().payload["raw_metrics"]["views"] == 12000.0


def test_feedback_api_queues_the_reward_and_refuses_a_conflicting_one() -> None:
    """The P9 route enqueues instead of posting: 200 and a PENDING event; the identical
    replay is 200; a different reward for the same fact is 409 reward_fact_conflict."""
    engine = create_analytics_engine(static_pool=True)
    log_allocation_decision(engine, directive_id=LINEAGE.production_directive_id,
                            arm_id=LINEAGE.content_arm_id, decided_at=NOW)
    factory = sessionmaker(bind=engine)
    app = create_analytics_app(session_factory=factory, settings=load_settings(env=ENV),
                               clock=FixedClock(NOW),
                               analytics_settings=AnalyticsSettings(reporting_currency="USD"))
    body = {
        "lineage": lineage_fields(), "unit_id": "vid_api_01",
        "net_contribution": {"amount": "25.00", "currency": "USD"}, "views": 10000,
        "watch_time_minutes": 320.0, "average_percentage_viewed": None,
        "subscribers_gained": None, "estimated_revenue": None, "window": "7d",
        "published_at": "2026-09-20T12:00:00+00:00", "attribution_confidence": 1.0,
        "quality_status": None, "observed_at": "2026-10-01T12:00:00+00:00",
    }
    headers = {SERVICE_TOKEN_HEADER: TOKEN}
    with TestClient(app) as client:
        assert client.post("/analytics/evaluation/feedback", json=body,
                           headers=headers).status_code == 200
        assert client.post("/analytics/evaluation/feedback", json=body,
                           headers=headers).status_code == 200
        conflict = client.post("/analytics/evaluation/feedback",
                               json={**body, "views": 20000}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "reward_fact_conflict"
    with factory() as session:
        (message,) = reward_outbox.messages(session)
    assert message.status is OutboxStatus.PENDING
    assert message.created_at == NOW  # the route's clock, not the observation stamp
    assert message.payload["raw_metrics"]["views"] == 10000.0
    engine.dispose()


# --- dispatch into P2's real app --------------------------------------------------------------


def test_end_to_end_enqueue_dispatch_and_p2_stores_it(loop: Loop) -> None:
    """P9 enqueue → dispatcher → HTTP transport → P2's real `/ar/results` → stored once;
    the reward counts as posted only after the acknowledgement."""
    payload = _enqueue(loop)
    report = _dispatch(loop)
    assert (report.claimed, report.delivered) == (1, 1)
    assert loop.p2_rows() == 1
    with loop.p9() as session:
        assert reward_delivery_status(session, payload) is OutboxStatus.DELIVERED
    lookup = loop.p2_client.get(AR_RESULTS_PATH, headers={SERVICE_TOKEN_HEADER: TOKEN}, params={
        "experiment_id": payload.experiment_id, "content_id": payload.content_id,
        "observation_window": "7d", "status": "mature"})
    assert lookup.status_code == 200 and lookup.json()["stored"] is True


def test_redelivery_after_a_crash_is_deduped_by_p2(loop: Loop) -> None:
    """The dispatcher dies after P2 stored the fact but before P9 committed DELIVERED.
    The row is still PENDING, so the next pass resends; P2 stores once and acknowledges
    both sends (the second as ``stored: false``)."""
    _enqueue(loop)
    with pytest.raises(Crash):
        _dispatch(loop, CrashAfterSend(_real_transport(loop)))
    assert loop.p2_rows() == 1
    assert loop.message().status is OutboxStatus.PENDING

    assert _dispatch(loop).delivered == 1
    assert loop.p2_rows() == 1
    message = loop.message()
    assert message.status is OutboxStatus.DELIVERED
    assert message.attempt_count == 1  # the crashed attempt never committed


def test_timeout_after_p2_stored_reconciles_to_delivered_without_resending(loop: Loop) -> None:
    _enqueue(loop)
    lost = LostResponse(_real_transport(loop), forward=True)
    assert _dispatch(loop, lost).uncertain == 1
    assert loop.message().status is OutboxStatus.UNCERTAIN

    loop.clock.set(loop.message().next_attempt_at)
    assert _dispatch(loop, lost).reconciled_present == 1  # P2's GET /ar/results said stored
    assert loop.message().status is OutboxStatus.DELIVERED
    assert lost.sent == 1
    assert loop.p2_rows() == 1


def test_timeout_before_p2_stored_reconciles_absent_and_resends(loop: Loop) -> None:
    _enqueue(loop)
    assert _dispatch(loop, LostResponse(_real_transport(loop), forward=False)).uncertain == 1
    loop.clock.set(loop.message().next_attempt_at)
    assert _dispatch(loop).reconciled_absent == 1
    assert loop.message().status is OutboxStatus.PENDING
    assert loop.p2_rows() == 0

    assert _dispatch(loop).delivered == 1
    assert loop.p2_rows() == 1
    assert loop.message().attempt_count == 2


def test_p2_refusal_fails_the_event_with_p2s_reason_code(loop: Loop) -> None:
    """P2 refuses a result for an arm it does not know (422 invalid_result): definite,
    so the event is FAILED, never retried, and the reward is never counted as posted."""
    payload = _enqueue(loop, arm_id="arm_unknown_to_p2", unit_id="vid_loop_02")
    assert _dispatch(loop).failed == 1
    message = loop.message()
    assert message.status is OutboxStatus.FAILED
    assert message.last_error_code == "invalid_result"
    loop.clock.advance(timedelta(days=7))
    assert _dispatch(loop).claimed == 0
    with loop.p9() as session:
        assert reward_delivery_status(session, payload) is OutboxStatus.FAILED
    assert loop.p2_rows() == 0


# --- P2's receiver surface ------------------------------------------------------------------


def test_p2_result_lookup_answers_stored_or_not_and_validates_its_query(loop: Loop) -> None:
    headers = {SERVICE_TOKEN_HEADER: TOKEN}
    params = {"experiment_id": LINEAGE.experiment_id, "content_id": "vid_loop_01",
              "observation_window": "7d", "status": "mature"}
    absent = loop.p2_client.get(AR_RESULTS_PATH, params=params, headers=headers)
    assert absent.status_code == 200 and absent.json()["stored"] is False

    payload = _enqueue(loop)
    first = loop.p2_client.post(AR_RESULTS_PATH, json=payload.to_wire(), headers=headers)
    replay = loop.p2_client.post(AR_RESULTS_PATH, json=payload.to_wire(), headers=headers)
    assert first.status_code == replay.status_code == 200
    assert (first.json()["stored"], replay.json()["stored"]) == (True, False)

    present = loop.p2_client.get(AR_RESULTS_PATH, params=params, headers=headers).json()
    assert present["stored"] is True
    assert present["observation_id"] == first.json()["observation_id"] == absent.json()[
        "observation_id"]
    provisional = loop.p2_client.get(AR_RESULTS_PATH, headers=headers,
                                     params={**params, "status": "provisional"}).json()
    assert provisional["stored"] is False  # maturity is part of the fact

    bad = loop.p2_client.get(AR_RESULTS_PATH, params={**params, "observation_window": "14d"},
                             headers=headers)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_query"
    assert loop.p2_client.get(AR_RESULTS_PATH, params=params).status_code == 401


# --- the migration ----------------------------------------------------------------------------


def test_migration_0026_creates_exactly_the_outbox_table_the_runtime_uses() -> None:
    """Drift guard (SQLite DDL, not Postgres): same columns, types, nullability, unique
    key and due index as `plm_platform.outbox.outbox_table`; downgrade removes it."""
    path = (Path(__file__).resolve().parents[2]
            / "migrations/versions/analytics/analytics_0026_outbox.py")
    spec = importlib.util.spec_from_file_location("analytics_0026_outbox", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0026_analytics_outbox", "0025_ops_schema")

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS analytics")
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        inspector = sa.inspect(connection)
        columns = {c["name"]: c
                   for c in inspector.get_columns("outbox_events", schema="analytics")}
        assert list(columns) == [c.name for c in OUTBOX_EVENTS.columns]
        for column in OUTBOX_EVENTS.columns:
            assert columns[column.name]["nullable"] == column.nullable, column.name
            assert str(columns[column.name]["type"]) == str(
                column.type.compile(dialect=connection.dialect)), column.name
        uniques = inspector.get_unique_constraints("outbox_events", schema="analytics")
        assert [u["column_names"] for u in uniques] == [["topic", "idempotency_key"]]
        indexes = inspector.get_indexes("outbox_events", schema="analytics")
        assert [(i["name"], i["column_names"]) for i in indexes] == [
            ("ix_analytics_outbox_events_due", ["status", "next_attempt_at"])]
        module.downgrade()
        assert "outbox_events" not in sa.inspect(connection).get_table_names(schema="analytics")
    engine.dispose()

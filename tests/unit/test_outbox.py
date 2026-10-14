"""`plm_platform.outbox`: the transactional outbox's states, transitions and HTTP edge.

What these prove: the state machine and the HTTP classification, in-process, over an
in-memory SQLite table and scripted fake transports (or `httpx.MockTransport`). They do
not prove a deployed dispatcher, Postgres row locking (``FOR UPDATE SKIP LOCKED`` is a
no-op on SQLite) or a real network. `test_reward_delivery.py` drives the same dispatcher
against P2's real ASGI app.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER
from plm_platform.outbox import (
    IDEMPOTENCY_KEY_HEADER,
    BackoffPolicy,
    Dispatcher,
    HttpReconciler,
    HttpTransport,
    Outbox,
    OutboxConflictError,
    OutboxMessage,
    OutboxStatus,
    ReconcileOutcome,
    ReconcileResult,
    SendOutcome,
    SendResult,
    outbox_table,
)
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
SCHEMA = "producer"
TOPIC = "test.topic"
DEST = "/receiver/facts"
BACKOFF = BackoffPolicy(base_seconds=10.0, factor=2.0, max_seconds=300.0, jitter_ratio=0.1)


class FakeTransport:
    """Returns scripted results in order and records every message it was asked to send."""

    def __init__(self, *results: SendResult | Exception) -> None:
        self.results = list(results)
        self.sent: list[OutboxMessage] = []

    def send(self, message: OutboxMessage) -> SendResult:
        self.sent.append(message)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeReconciler:
    def __init__(self, *results: ReconcileResult) -> None:
        self.results = list(results)
        self.asked: list[str] = []

    def lookup(self, message: OutboxMessage) -> ReconcileResult:
        self.asked.append(message.idempotency_key)
        return self.results.pop(0)


ACK = SendResult(SendOutcome.ACKNOWLEDGED, status_code=200)
TIMEOUT = SendResult(SendOutcome.AMBIGUOUS, code="ReadTimeout", detail="timed out")
SERVER_ERROR = SendResult(SendOutcome.TRANSIENT, code="internal_error", status_code=500)
REFUSED = SendResult(SendOutcome.REFUSED, code="invalid_result", detail="bad", status_code=422)


class Harness:
    def __init__(self) -> None:
        self.engine = sa.create_engine(
            "sqlite+pysqlite:///:memory:", poolclass=sa.pool.StaticPool,
            connect_args={"check_same_thread": False},
        )
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {SCHEMA}")
        self.metadata = sa.MetaData()
        self.facts = sa.Table("facts", self.metadata,
                              sa.Column("id", sa.String(32), primary_key=True), schema=SCHEMA)
        self.table = outbox_table(self.metadata, SCHEMA)
        self.metadata.create_all(self.engine)
        self.factory: Callable[[], Session] = sessionmaker(bind=self.engine)
        self.outbox = Outbox(self.table)
        self.clock = FixedClock(NOW)

    def enqueue(self, key: str = "fact-1", payload: dict[str, Any] | None = None) -> None:
        with self.factory() as session:
            self.outbox.enqueue(session, topic=TOPIC, idempotency_key=key,
                                payload=payload or {"fact": key, "value": 1}, destination=DEST,
                                now=self.clock.now())
            session.commit()

    def dispatcher(self, transport: FakeTransport,
                   reconciler: FakeReconciler | None = None) -> Dispatcher:
        return Dispatcher(self.outbox, transport=transport,
                          reconciler=reconciler or FakeReconciler(), clock=self.clock,
                          backoff=BACKOFF)

    def get(self, key: str = "fact-1") -> OutboxMessage:
        with self.factory() as session:
            message = self.outbox.get(session, topic=TOPIC, idempotency_key=key)
        assert message is not None
        return message


@pytest.fixture
def harness() -> Iterator[Harness]:
    built = Harness()
    yield built
    built.engine.dispose()


# --- enqueue -------------------------------------------------------------------------------


def test_enqueue_joins_the_callers_transaction_and_never_commits(harness: Harness) -> None:
    """Proves atomicity at the outbox level: rollback removes the fact and the event."""
    with harness.factory() as session:
        session.execute(sa.insert(harness.facts).values(id="fact-1"))
        harness.outbox.enqueue(session, topic=TOPIC, idempotency_key="fact-1",
                               payload={"fact": "fact-1"}, destination=DEST, now=NOW)
        session.rollback()
    with harness.factory() as session:
        assert session.execute(sa.select(harness.facts)).first() is None
        assert harness.outbox.messages(session) == []

    with harness.factory() as session:
        session.execute(sa.insert(harness.facts).values(id="fact-1"))
        harness.outbox.enqueue(session, topic=TOPIC, idempotency_key="fact-1",
                               payload={"fact": "fact-1"}, destination=DEST, now=NOW)
        session.commit()
    message = harness.get()
    assert message.status is OutboxStatus.PENDING
    assert message.attempt_count == 0
    assert message.next_attempt_at == NOW


def test_same_key_is_idempotent_and_a_different_payload_is_refused(harness: Harness) -> None:
    harness.enqueue(payload={"fact": "fact-1", "value": 1})
    harness.enqueue(payload={"value": 1, "fact": "fact-1"})  # same canonical payload
    with harness.factory() as session:
        assert len(harness.outbox.messages(session)) == 1
        with pytest.raises(OutboxConflictError):
            harness.outbox.enqueue(session, topic=TOPIC, idempotency_key="fact-1",
                                   payload={"fact": "fact-1", "value": 2}, destination=DEST,
                                   now=NOW)
        with pytest.raises(OutboxConflictError):
            harness.outbox.enqueue(session, topic=TOPIC, idempotency_key="fact-1",
                                   payload={"fact": "fact-1", "value": 1}, destination="/other",
                                   now=NOW)


# --- dispatch transitions -------------------------------------------------------------------


def test_acknowledgement_is_the_only_way_a_send_becomes_delivered(harness: Harness) -> None:
    harness.enqueue()
    transport = FakeTransport(ACK)
    report = harness.dispatcher(transport).run_once(harness.factory)
    message = harness.get()
    assert (report.claimed, report.delivered) == (1, 1)
    assert message.status is OutboxStatus.DELIVERED
    assert message.attempt_count == 1
    assert message.delivered_at == NOW and message.last_attempt_at == NOW
    assert message.last_outcome == "acknowledged"
    # A delivered row is never claimed again.
    assert harness.dispatcher(FakeTransport()).run_once(harness.factory).claimed == 0


def test_definite_refusal_fails_with_the_receivers_reason_and_is_never_retried(
    harness: Harness,
) -> None:
    harness.enqueue()
    transport = FakeTransport(REFUSED)
    harness.dispatcher(transport).run_once(harness.factory)
    message = harness.get()
    assert message.status is OutboxStatus.FAILED
    assert message.last_error_code == "invalid_result"
    assert message.attempt_count == 1

    harness.clock.advance(timedelta(days=30))
    later = FakeTransport()  # would raise IndexError if asked to send
    assert harness.dispatcher(later).run_once(harness.factory).claimed == 0
    assert later.sent == []


def test_transient_errors_back_off_on_a_deterministic_schedule(harness: Harness) -> None:
    """5xx ⇒ PENDING with exponential backoff; the schedule is a pure function of key and
    attempt (stable_seed jitter), identical in a second, independent run."""

    def schedule() -> list[timedelta]:
        run = Harness()
        run.enqueue()
        delays: list[timedelta] = []
        for _ in range(6):
            sent_at = run.clock.now()
            run.dispatcher(FakeTransport(SERVER_ERROR)).run_once(run.factory)
            message = run.get()
            assert message.status is OutboxStatus.PENDING
            assert message.last_error_code == "internal_error"
            delays.append(message.next_attempt_at - sent_at)
            # Not due a millisecond early; due exactly on time.
            run.clock.set(message.next_attempt_at - timedelta(milliseconds=1))
            assert run.dispatcher(FakeTransport()).run_once(run.factory).claimed == 0
            run.clock.set(message.next_attempt_at)
        assert run.get().attempt_count == 6
        run.engine.dispose()
        return delays

    first = schedule()
    assert first == schedule()
    expected = [BACKOFF.delay("plm.outbox.send", "fact-1", n) for n in range(1, 7)]
    assert first == expected
    nominal = [10, 20, 40, 80, 160, 300]
    for delay, base in zip(first, nominal, strict=True):
        assert base * 0.9 <= delay.total_seconds() <= min(base * 1.1, 300)


def test_timeout_is_uncertain_and_reconciliation_finds_it_without_resending(
    harness: Harness,
) -> None:
    harness.enqueue()
    transport = FakeTransport(TIMEOUT)
    reconciler = FakeReconciler(ReconcileResult(ReconcileOutcome.PRESENT))
    dispatcher = harness.dispatcher(transport, reconciler)

    assert dispatcher.run_once(harness.factory).uncertain == 1
    message = harness.get()
    assert message.status is OutboxStatus.UNCERTAIN
    assert message.delivered_at is None  # never assumed delivered

    harness.clock.set(message.next_attempt_at)
    report = dispatcher.run_once(harness.factory)
    assert report.reconciled_present == 1
    message = harness.get()
    assert message.status is OutboxStatus.DELIVERED
    assert message.reconcile_count == 1
    assert len(transport.sent) == 1  # reconciled, not resent
    assert reconciler.asked == ["fact-1"]


def test_timeout_then_definitely_absent_goes_back_to_pending_and_is_resent(
    harness: Harness,
) -> None:
    harness.enqueue()
    transport = FakeTransport(TIMEOUT, ACK)
    reconciler = FakeReconciler(ReconcileResult(ReconcileOutcome.ABSENT))
    dispatcher = harness.dispatcher(transport, reconciler)
    dispatcher.run_once(harness.factory)
    harness.clock.set(harness.get().next_attempt_at)

    assert dispatcher.run_once(harness.factory).reconciled_absent == 1
    message = harness.get()
    assert message.status is OutboxStatus.PENDING
    assert message.next_attempt_at == harness.clock.now()  # resend is due at once

    assert dispatcher.run_once(harness.factory).delivered == 1
    assert [m.idempotency_key for m in transport.sent] == ["fact-1", "fact-1"]
    assert harness.get().attempt_count == 2


def test_an_unanswerable_reconciliation_stays_uncertain(harness: Harness) -> None:
    harness.enqueue()
    reconciler = FakeReconciler(
        ReconcileResult(ReconcileOutcome.UNKNOWN, code="http_502", detail="bad gateway"),
        ReconcileResult(ReconcileOutcome.UNKNOWN, code="ReadTimeout"),
    )
    dispatcher = harness.dispatcher(FakeTransport(TIMEOUT), reconciler)
    dispatcher.run_once(harness.factory)
    for expected in (1, 2):
        harness.clock.set(harness.get().next_attempt_at)
        assert dispatcher.run_once(harness.factory).reconcile_unknown == 1
        message = harness.get()
        assert message.status is OutboxStatus.UNCERTAIN
        assert message.reconcile_count == expected
        assert message.next_attempt_at > harness.clock.now()
    assert harness.get().last_error_code == "ReadTimeout"


def test_a_transport_that_raises_is_ambiguous_never_failed(harness: Harness) -> None:
    harness.enqueue()
    harness.dispatcher(FakeTransport(RuntimeError("socket closed"))).run_once(harness.factory)
    message = harness.get()
    assert message.status is OutboxStatus.UNCERTAIN
    assert message.last_error_code == "transport_exception"


def test_claims_are_in_deterministic_order_and_respect_the_limit(harness: Harness) -> None:
    for key in ("c", "a", "b"):
        harness.enqueue(key)
    transport = FakeTransport(ACK, ACK, ACK)
    assert harness.dispatcher(transport).run_once(harness.factory, limit=2).claimed == 2
    harness.dispatcher(transport).run_once(harness.factory)
    ids = [m.id for m in transport.sent]
    assert ids[:2] == sorted(ids[:2])  # same next_attempt_at: ordered by id
    assert {m.idempotency_key for m in transport.sent} == {"a", "b", "c"}


def test_backoff_policy_refuses_nonsense() -> None:
    with pytest.raises(ValueError):
        BackoffPolicy(base_seconds=0)
    with pytest.raises(ValueError):
        BackoffPolicy(jitter_ratio=1.0)
    with pytest.raises(ValueError):
        BACKOFF.delay("ns", "k", 0)


# --- the HTTP edge --------------------------------------------------------------------------


def _message(payload: dict[str, Any] | None = None) -> OutboxMessage:
    return OutboxMessage(
        id="obx_1", topic=TOPIC, idempotency_key="fact-1", destination=DEST,
        payload=payload or {"fact": "fact-1"}, payload_sha256="x",
        status=OutboxStatus.PENDING, attempt_count=0, reconcile_count=0, next_attempt_at=NOW,
        last_attempt_at=None, last_outcome=None, last_error_code=None, last_error_detail=None,
        created_at=NOW, updated_at=NOW, delivered_at=None,
    )


def _http(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(base_url="http://receiver.test", transport=httpx.MockTransport(handler))


def _acks(body: Any) -> bool:
    return isinstance(body, dict) and isinstance(body.get("stored"), bool)


def _send(handler: Callable[[httpx.Request], httpx.Response]) -> SendResult:
    with _http(handler) as client:
        return HttpTransport(client, token="tok", acknowledges=_acks).send(_message())


def test_http_transport_sends_the_token_and_the_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"stored": False})

    assert _send(handler).outcome is SendOutcome.ACKNOWLEDGED  # a replay ack counts
    assert seen[0].method == "POST" and seen[0].url.path == DEST
    assert seen[0].headers[SERVICE_TOKEN_HEADER] == "tok"
    assert seen[0].headers[IDEMPOTENCY_KEY_HEADER] == "fact-1"


@pytest.mark.parametrize(
    ("response", "outcome", "code"),
    [
        (httpx.Response(200, json={"unexpected": True}), SendOutcome.AMBIGUOUS,
         "unrecognised_acknowledgement"),
        (httpx.Response(422, json={"detail": {"code": "invalid_result", "field_paths": [],
                                              "message": "bad"}}),
         SendOutcome.REFUSED, "invalid_result"),
        (httpx.Response(404, text="not found"), SendOutcome.REFUSED, "http_404"),
        (httpx.Response(503, json={"detail": {"code": "no_database", "field_paths": [],
                                              "message": "down"}}),
         SendOutcome.TRANSIENT, "no_database"),
        (httpx.Response(401, json={"detail": {"code": "invalid_service_token",
                                              "field_paths": [], "message": "no"}}),
         SendOutcome.TRANSIENT, "invalid_service_token"),
        (httpx.Response(429, text="slow down"), SendOutcome.TRANSIENT, "http_429"),
    ],
)
def test_http_transport_classifies_responses(response: httpx.Response, outcome: SendOutcome,
                                             code: str) -> None:
    result = _send(lambda request: response)
    assert (result.outcome, result.code) == (outcome, code)


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (httpx.ConnectError("refused"), SendOutcome.TRANSIENT),
        (httpx.ConnectTimeout("connect"), SendOutcome.TRANSIENT),
        (httpx.ReadTimeout("read"), SendOutcome.AMBIGUOUS),
        (httpx.RemoteProtocolError("dropped"), SendOutcome.AMBIGUOUS),
    ],
)
def test_http_transport_separates_never_sent_from_maybe_landed(error: Exception,
                                                               outcome: SendOutcome) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    assert _send(handler).outcome is outcome


@pytest.mark.parametrize(
    ("respond", "outcome"),
    [
        (lambda r: httpx.Response(200, json={"stored": True}), ReconcileOutcome.PRESENT),
        (lambda r: httpx.Response(200, json={"stored": False}), ReconcileOutcome.ABSENT),
        (lambda r: httpx.Response(200, json={"maybe": 1}), ReconcileOutcome.UNKNOWN),
        (lambda r: httpx.Response(500, text="boom"), ReconcileOutcome.UNKNOWN),
    ],
)
def test_http_reconciler_answers_only_what_the_receiver_states(
    respond: Callable[[httpx.Request], httpx.Response], outcome: ReconcileOutcome,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(request)

    def present(body: Any) -> bool | None:
        return body.get("stored") if isinstance(body, dict) and "stored" in body else None

    with _http(handler) as client:
        reconciler = HttpReconciler(client, token="tok",
                                    query=lambda m: (DEST, {"fact": str(m.payload["fact"])}),
                                    present=present)
        assert reconciler.lookup(_message()).outcome is outcome
    assert seen[0].method == "GET" and seen[0].url.params["fact"] == "fact-1"
    assert seen[0].headers[SERVICE_TOKEN_HEADER] == "tok"


def test_http_edge_refuses_to_run_without_a_token() -> None:
    with _http(lambda r: httpx.Response(200)) as client, pytest.raises(ValueError):
        HttpTransport(client, token="", acknowledges=_acks)

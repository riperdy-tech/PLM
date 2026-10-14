"""Transactional outbox: an event commits with the fact it announces, then is delivered.

Stage R3 (remediation plan, "Transactional outbox"; ADR-0033). A producer that announces a
fact to another phase over HTTP must not send it inside the request that records the
fact: the send can fail after the commit, or succeed before a rollback. Instead:

1. **Enqueue** (:meth:`Outbox.enqueue`) writes one row in the producer's own transaction
   and never commits. The fact and its event commit together or not at all. Each producer
   keeps its outbox in its own schema (:func:`outbox_table`); ``ops`` is not used.
2. **Dispatch** (:class:`Dispatcher`) claims due rows in a deterministic order, sends each
   through an injected :class:`Transport` with a stable idempotency key, and records the
   outcome. The receiver dedupes on that key, so a resend can never count a fact twice.

States and transitions (:class:`OutboxStatus`)::

    PENDING   --acknowledged (2xx, expected body)------------------> DELIVERED
    PENDING   --refused (definite 4xx, receiver reason code)-------> FAILED     (no retry)
    PENDING   --transient (5xx, connection refused, 429)-----------> PENDING    (backoff)
    PENDING   --ambiguous (timeout; the request may have landed)---> UNCERTAIN
    UNCERTAIN --reconcile: the receiver holds the fact-------------> DELIVERED  (no resend)
    UNCERTAIN --reconcile: the receiver definitely lacks it--------> PENDING    (resend now)
    UNCERTAIN --reconcile: the receiver cannot answer--------------> UNCERTAIN  (backoff)

An event is ``DELIVERED`` only on an explicit acknowledgement or a positive
reconciliation, never assumed. ``UNCERTAIN`` is never resolved by guessing. A transient
failure never becomes ``FAILED``: only the receiver can refuse a payload.

Every send and every reconciliation is recorded on the row (counts, last outcome, last
error code and detail, timestamps). Time comes from an injected
:class:`~plm_platform.clock.Clock`; backoff jitter comes from
:func:`~plm_platform.clock.stable_seed`, so a schedule replays identically.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session

from .clock import SEED_MODULUS, Clock, require_aware, stable_seed
from .config import SERVICE_TOKEN_HEADER

__all__ = [
    "IDEMPOTENCY_KEY_HEADER",
    "OUTBOX_TABLE_NAME",
    "BackoffPolicy",
    "DispatchReport",
    "Dispatcher",
    "HttpReconciler",
    "HttpTransport",
    "Outbox",
    "OutboxConflictError",
    "OutboxMessage",
    "OutboxStatus",
    "ReconcileOutcome",
    "ReconcileResult",
    "Reconciler",
    "SendOutcome",
    "SendResult",
    "Transport",
    "outbox_table",
    "payload_digest",
]

OUTBOX_TABLE_NAME: Final[str] = "outbox_events"
IDEMPOTENCY_KEY_HEADER: Final[str] = "Idempotency-Key"

_DETAIL_LIMIT: Final[int] = 1024


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class SendOutcome(StrEnum):
    """What one send established about the receiver."""

    ACKNOWLEDGED = "acknowledged"
    """The receiver confirmed it holds the fact (2xx with the expected body)."""
    REFUSED = "refused"
    """The receiver definitely refused the payload; resending cannot change that."""
    TRANSIENT = "transient"
    """Definitely not processed, or safe to resend (5xx, connection refused, 429)."""
    AMBIGUOUS = "ambiguous"
    """The request may have landed (timeout, dropped response): reconcile, never guess."""


class ReconcileOutcome(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SendResult:
    outcome: SendOutcome
    code: str | None = None
    """The receiver's reason code (ErrorShape ``code``) or the transport's error class."""
    detail: str | None = None
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    code: str | None = None
    detail: str | None = None


class OutboxConflictError(RuntimeError):
    """The idempotency key is already queued with a different payload or destination.

    One key names one fact. Two different payloads under one key would let the receiver
    keep whichever arrived first, so the second enqueue is refused, not merged.
    """


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: str
    topic: str
    idempotency_key: str
    destination: str
    """Where the transport sends it, relative to the transport's base (e.g. a route path)."""
    payload: Mapping[str, Any]
    payload_sha256: str
    status: OutboxStatus
    attempt_count: int
    reconcile_count: int
    next_attempt_at: datetime
    last_attempt_at: datetime | None
    last_outcome: str | None
    last_error_code: str | None
    last_error_detail: str | None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None


class Transport(Protocol):
    """Sends one message. Must classify, not raise; a raise is treated as ambiguous."""

    def send(self, message: OutboxMessage) -> SendResult: ...


class Reconciler(Protocol):
    """Asks the receiver whether the fact named by the message's key exists."""

    def lookup(self, message: OutboxMessage) -> ReconcileResult: ...


def outbox_table(metadata: sa.MetaData, schema: str, *, name: str = OUTBOX_TABLE_NAME) -> sa.Table:
    """The outbox table in the producer's own ``schema`` (ADR-0033), on ``metadata``.

    The producer's migration must create the same DDL; its drift test compares the two.
    """
    table = sa.Table(
        name, metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False),
        sa.Column("reconcile_count", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(32), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_detail", sa.String(_DETAIL_LIMIT), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("topic", "idempotency_key", name=f"uq_{name}_topic_key"),
        schema=schema,
    )
    sa.Index(f"ix_{schema}_{name}_due", table.c.status, table.c.next_attempt_at)
    return table


def payload_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON (sorted keys, no whitespace, no NaN)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                           allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _message_id(topic: str, idempotency_key: str) -> str:
    return "obx_" + hashlib.sha256(f"{topic}|{idempotency_key}".encode()).hexdigest()[:32]


def _aware(value: datetime | None) -> datetime | None:
    """SQLite round-trips ``timezone=True`` columns naive; stored values are always UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _clip(text: str | None) -> str | None:
    return None if text is None else text[:_DETAIL_LIMIT]


class Outbox:
    """Enqueue and read one producer's outbox table."""

    def __init__(self, table: sa.Table) -> None:
        self.table = table

    def enqueue(self, session: Session, *, topic: str, idempotency_key: str,
                payload: Mapping[str, Any], destination: str, now: datetime) -> OutboxMessage:
        """Add the event to the caller's transaction. Never commits, never sends.

        The same key with the same payload and destination returns the stored message
        unchanged (whatever its status). The same key with anything different raises
        :class:`OutboxConflictError`.
        """
        if not topic or not idempotency_key or not destination:
            raise ValueError("topic, idempotency_key and destination are required")
        moment = require_aware(now)
        body = dict(payload)
        digest = payload_digest(body)
        existing = self.get(session, topic=topic, idempotency_key=idempotency_key)
        if existing is not None:
            if existing.payload_sha256 != digest or existing.destination != destination:
                raise OutboxConflictError(
                    f"{topic} key {idempotency_key!r} is already queued with a different "
                    "payload or destination; one key names one fact"
                )
            return existing
        message_id = _message_id(topic, idempotency_key)
        session.execute(sa.insert(self.table).values(
            id=message_id, topic=topic, idempotency_key=idempotency_key,
            destination=destination, payload_json=body, payload_sha256=digest,
            status=OutboxStatus.PENDING.value, attempt_count=0, reconcile_count=0,
            next_attempt_at=moment, last_attempt_at=None, last_outcome=None,
            last_error_code=None, last_error_detail=None, created_at=moment,
            updated_at=moment, delivered_at=None,
        ))
        stored = self.get(session, topic=topic, idempotency_key=idempotency_key)
        assert stored is not None  # inserted above in this transaction
        return stored

    def get(self, session: Session, *, topic: str, idempotency_key: str) -> OutboxMessage | None:
        row = session.execute(
            sa.select(self.table).where(self.table.c.topic == topic,
                                        self.table.c.idempotency_key == idempotency_key)
        ).mappings().one_or_none()
        return None if row is None else _message(row)

    def messages(self, session: Session, *, status: OutboxStatus | None = None,
                 topic: str | None = None) -> list[OutboxMessage]:
        query = sa.select(self.table)
        if status is not None:
            query = query.where(self.table.c.status == status.value)
        if topic is not None:
            query = query.where(self.table.c.topic == topic)
        query = query.order_by(self.table.c.created_at, self.table.c.id)
        return [_message(row) for row in session.execute(query).mappings()]


def _message(row: Any) -> OutboxMessage:
    return OutboxMessage(
        id=row["id"], topic=row["topic"], idempotency_key=row["idempotency_key"],
        destination=row["destination"], payload=row["payload_json"],
        payload_sha256=row["payload_sha256"], status=OutboxStatus(row["status"]),
        attempt_count=int(row["attempt_count"]), reconcile_count=int(row["reconcile_count"]),
        next_attempt_at=_aware(row["next_attempt_at"]),  # type: ignore[arg-type]
        last_attempt_at=_aware(row["last_attempt_at"]), last_outcome=row["last_outcome"],
        last_error_code=row["last_error_code"], last_error_detail=row["last_error_detail"],
        created_at=_aware(row["created_at"]),  # type: ignore[arg-type]
        updated_at=_aware(row["updated_at"]),  # type: ignore[arg-type]
        delivered_at=_aware(row["delivered_at"]),
    )


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Exponential backoff with deterministic jitter.

    ``delay(n) = min(max, base * factor**(n-1)) * (1 + jitter * u)`` where
    ``u in [-1, 1)`` derives from ``stable_seed(namespace, key, n)``, then capped at
    ``max_seconds``. The same key and attempt give the same delay in every process.
    """

    base_seconds: float = 30.0
    factor: float = 2.0
    max_seconds: float = 3600.0
    jitter_ratio: float = 0.1

    def __post_init__(self) -> None:
        if self.base_seconds <= 0 or self.factor < 1 or self.max_seconds < self.base_seconds:
            raise ValueError("backoff needs base > 0, factor >= 1 and max >= base")
        if not 0 <= self.jitter_ratio < 1:
            raise ValueError("jitter_ratio must be in [0, 1)")

    def delay(self, namespace: str, key: str, attempt: int) -> timedelta:
        if attempt < 1:
            raise ValueError("attempt numbers start at 1")
        exponent = min(attempt - 1, 64)  # beyond this the cap applies anyway
        raw = min(self.max_seconds, self.base_seconds * self.factor**exponent)
        unit = stable_seed(namespace, key, attempt) / SEED_MODULUS  # [0, 1)
        jittered = raw * (1.0 + self.jitter_ratio * (2.0 * unit - 1.0))
        return timedelta(milliseconds=round(min(self.max_seconds, jittered) * 1000))


@dataclass(frozen=True, slots=True)
class DispatchReport:
    claimed: int = 0
    delivered: int = 0
    failed: int = 0
    retry_scheduled: int = 0
    uncertain: int = 0
    reconciled_present: int = 0
    reconciled_absent: int = 0
    reconcile_unknown: int = 0
    skipped: int = 0
    """Claimed by id but no longer due when locked (another dispatcher took it)."""


_SEND_NAMESPACE: Final[str] = "plm.outbox.send"
_RECONCILE_NAMESPACE: Final[str] = "plm.outbox.reconcile"
_DUE: Final[tuple[str, ...]] = (OutboxStatus.PENDING.value, OutboxStatus.UNCERTAIN.value)


class Dispatcher:
    """Claims due messages and moves each through one send or one reconciliation."""

    def __init__(self, outbox: Outbox, *, transport: Transport, reconciler: Reconciler,
                 clock: Clock, backoff: BackoffPolicy | None = None,
                 topics: tuple[str, ...] | None = None) -> None:
        self._outbox = outbox
        self._table = outbox.table
        self._transport = transport
        self._reconciler = reconciler
        self._clock = clock
        self._backoff = backoff or BackoffPolicy()
        self._topics = topics

    def run_once(self, session_factory: Callable[[], Session], *, limit: int = 100) -> DispatchReport:
        """One pass: every message due at the clock's instant, at most ``limit``.

        Order is ``(next_attempt_at, id)``. Each message is handled in its own
        transaction holding its row lock (``FOR UPDATE SKIP LOCKED`` where supported), so
        concurrent dispatchers never send the same row at once, and a crash before the
        commit leaves the row due for a resend the receiver dedupes.
        """
        if limit < 1:
            raise ValueError("limit must be positive")
        now = require_aware(self._clock.now())
        with session_factory() as session:
            query = (
                sa.select(self._table.c.id)
                .where(self._table.c.status.in_(_DUE), self._table.c.next_attempt_at <= now)
                .order_by(self._table.c.next_attempt_at, self._table.c.id)
                .limit(limit)
            )
            if self._topics is not None:
                query = query.where(self._table.c.topic.in_(self._topics))
            ids = list(session.execute(query).scalars())
        counts: dict[str, int] = {"claimed": len(ids)}
        for message_id in ids:
            with session_factory() as session:
                row = session.execute(
                    sa.select(self._table)
                    .where(self._table.c.id == message_id, self._table.c.status.in_(_DUE),
                           self._table.c.next_attempt_at <= now)
                    .with_for_update(skip_locked=True)
                ).mappings().one_or_none()
                if row is None:
                    _bump(counts, "skipped")
                    continue
                message = _message(row)
                if message.status is OutboxStatus.PENDING:
                    _bump(counts, self._send(session, message))
                else:
                    _bump(counts, self._reconcile(session, message))
                session.commit()
        return DispatchReport(**counts)

    def _send(self, session: Session, message: OutboxMessage) -> str:
        try:
            result = self._transport.send(message)
        except Exception as exc:  # noqa: BLE001 - an unclassified raise may have landed
            result = SendResult(SendOutcome.AMBIGUOUS, code="transport_exception",
                                detail=f"{type(exc).__name__}: {exc}")
        at = require_aware(self._clock.now())
        attempt = message.attempt_count + 1
        values: dict[str, Any] = {
            "attempt_count": attempt, "last_attempt_at": at, "updated_at": at,
            "last_outcome": result.outcome.value,
        }
        if result.outcome is not SendOutcome.ACKNOWLEDGED:
            values["last_error_code"] = result.code
            values["last_error_detail"] = _clip(result.detail)
        if result.outcome is SendOutcome.ACKNOWLEDGED:
            values.update(status=OutboxStatus.DELIVERED.value, delivered_at=at)
            counter = "delivered"
        elif result.outcome is SendOutcome.REFUSED:
            values.update(status=OutboxStatus.FAILED.value,
                          last_error_code=result.code or "refused")
            counter = "failed"
        elif result.outcome is SendOutcome.TRANSIENT:
            values.update(status=OutboxStatus.PENDING.value, next_attempt_at=at
                          + self._backoff.delay(_SEND_NAMESPACE, message.idempotency_key, attempt))
            counter = "retry_scheduled"
        else:
            # Reconcile after the same backoff: give an in-flight request time to land.
            values.update(status=OutboxStatus.UNCERTAIN.value, next_attempt_at=at
                          + self._backoff.delay(_SEND_NAMESPACE, message.idempotency_key, attempt))
            counter = "uncertain"
        self._update(session, message.id, values)
        return counter

    def _reconcile(self, session: Session, message: OutboxMessage) -> str:
        try:
            result = self._reconciler.lookup(message)
        except Exception as exc:  # noqa: BLE001 - a failed lookup answers nothing
            result = ReconcileResult(ReconcileOutcome.UNKNOWN, code="reconciler_exception",
                                     detail=f"{type(exc).__name__}: {exc}")
        at = require_aware(self._clock.now())
        count = message.reconcile_count + 1
        values: dict[str, Any] = {
            "reconcile_count": count, "updated_at": at,
            "last_outcome": f"reconcile_{result.outcome.value}",
        }
        if result.outcome is ReconcileOutcome.PRESENT:
            values.update(status=OutboxStatus.DELIVERED.value, delivered_at=at)
            counter = "reconciled_present"
        elif result.outcome is ReconcileOutcome.ABSENT:
            values.update(status=OutboxStatus.PENDING.value, next_attempt_at=at)
            counter = "reconciled_absent"
        else:
            values.update(
                status=OutboxStatus.UNCERTAIN.value,
                next_attempt_at=at + self._backoff.delay(
                    _RECONCILE_NAMESPACE, message.idempotency_key, count),
                last_error_code=result.code, last_error_detail=_clip(result.detail),
            )
            counter = "reconcile_unknown"
        self._update(session, message.id, values)
        return counter

    def _update(self, session: Session, message_id: str, values: dict[str, Any]) -> None:
        session.execute(sa.update(self._table).where(self._table.c.id == message_id)
                        .values(**values))


def _bump(counts: dict[str, int], name: str) -> None:
    counts[name] = counts.get(name, 0) + 1


# --- HTTP edge ---------------------------------------------------------------------------

_RETRYABLE_4XX: Final[frozenset[int]] = frozenset({401, 403, 408, 425, 429})
"""4xx answers that refuse the request, not the payload: a bad or rotated service token
(401/403), a request timeout (408), too early (425) or rate limiting (429). Retrying can
succeed once the condition clears, so they are transient, never ``FAILED``."""


def _error_code(response: httpx.Response) -> tuple[str, str]:
    """The ErrorShape ``code`` and message from ``{"detail": {...}}`` or a bare shape."""
    try:
        body = response.json()
    except ValueError:
        return f"http_{response.status_code}", response.text[:_DETAIL_LIMIT]
    shape = body.get("detail", body) if isinstance(body, dict) else None
    if isinstance(shape, dict) and isinstance(shape.get("code"), str):
        return shape["code"], str(shape.get("message", ""))
    return f"http_{response.status_code}", json.dumps(body)[:_DETAIL_LIMIT]


class HttpTransport:
    """POSTs the payload to ``message.destination`` on ``client`` (which owns the base URL
    and the timeout), with the service token and the ``Idempotency-Key`` header.

    Classification: 2xx with a body ``acknowledges`` accepts is ``ACKNOWLEDGED``; any
    other 2xx is ``AMBIGUOUS``. Connection refused, connect/pool timeout (nothing was
    sent), 5xx and the 4xx in :data:`_RETRYABLE_4XX` are ``TRANSIENT``; a resend is safe
    because the receiver dedupes on the key. Any other 4xx is ``REFUSED`` with the
    receiver's reason code. A read/write timeout or a dropped connection after the request
    went out is ``AMBIGUOUS``.
    """

    def __init__(self, client: httpx.Client, *, token: str,
                 acknowledges: Callable[[Any], bool]) -> None:
        if not token:
            raise ValueError("a service token is required (R15); none was configured")
        self._client = client
        self._token = token
        self._acknowledges = acknowledges

    def send(self, message: OutboxMessage) -> SendResult:
        headers = {SERVICE_TOKEN_HEADER: self._token,
                   IDEMPOTENCY_KEY_HEADER: message.idempotency_key}
        try:
            response = self._client.post(message.destination, json=dict(message.payload),
                                         headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            return SendResult(SendOutcome.TRANSIENT, code=type(exc).__name__, detail=str(exc))
        except httpx.HTTPError as exc:
            return SendResult(SendOutcome.AMBIGUOUS, code=type(exc).__name__, detail=str(exc))
        status = response.status_code
        if 200 <= status < 300:
            try:
                body = response.json()
            except ValueError:
                body = None
            if self._acknowledges(body):
                return SendResult(SendOutcome.ACKNOWLEDGED, status_code=status)
            return SendResult(SendOutcome.AMBIGUOUS, code="unrecognised_acknowledgement",
                              detail=response.text[:_DETAIL_LIMIT], status_code=status)
        code, detail = _error_code(response)
        if status >= 500 or status in _RETRYABLE_4XX:
            return SendResult(SendOutcome.TRANSIENT, code=code, detail=detail, status_code=status)
        if 400 <= status < 500:
            return SendResult(SendOutcome.REFUSED, code=code, detail=detail, status_code=status)
        return SendResult(SendOutcome.AMBIGUOUS, code=code, detail=detail, status_code=status)


class HttpReconciler:
    """GETs ``query(message) -> (path, params)`` and maps the body through ``present``.

    ``present(body)`` returns ``True`` (stored), ``False`` (definitely not stored) or
    ``None`` (the answer is not understood). Anything but a 200 with an understood body
    is ``UNKNOWN``: the row stays ``UNCERTAIN``.
    """

    def __init__(self, client: httpx.Client, *, token: str,
                 query: Callable[[OutboxMessage], tuple[str, Mapping[str, str]]],
                 present: Callable[[Any], bool | None]) -> None:
        if not token:
            raise ValueError("a service token is required (R15); none was configured")
        self._client = client
        self._token = token
        self._query = query
        self._present = present

    def lookup(self, message: OutboxMessage) -> ReconcileResult:
        path, params = self._query(message)
        try:
            response = self._client.get(path, params=dict(params),
                                        headers={SERVICE_TOKEN_HEADER: self._token})
        except httpx.HTTPError as exc:
            return ReconcileResult(ReconcileOutcome.UNKNOWN, code=type(exc).__name__,
                                   detail=str(exc))
        if response.status_code != 200:
            code, detail = _error_code(response)
            return ReconcileResult(ReconcileOutcome.UNKNOWN, code=code, detail=detail)
        try:
            verdict = self._present(response.json())
        except ValueError:
            verdict = None
        if verdict is True:
            return ReconcileResult(ReconcileOutcome.PRESENT)
        if verdict is False:
            return ReconcileResult(ReconcileOutcome.ABSENT)
        return ReconcileResult(ReconcileOutcome.UNKNOWN, code="unrecognised_lookup",
                               detail=response.text[:_DETAIL_LIMIT])

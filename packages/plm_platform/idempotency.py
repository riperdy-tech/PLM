"""Replayable HTTP mutations keyed by the ``Idempotency-Key`` header (R5).

A caller that loses a response retries with the same key. Without a record of the first
answer the retry re-executes, and a phase whose writes have deterministic ids then fails
closed on its own duplicate insert, so the caller can never learn what happened. This
module stores, per key, the request's fingerprint and the response that was returned:

* the **same key with the same request** replays the stored response, status included,
  and changes nothing;
* the **same key with a different request** is refused (``idempotency_key_reused``):
  a key names one request, and reusing it for another is a caller bug, never a replay;
* a request **without a key** runs as before.

The record lives in the service's own schema (ADR-0033), written in the same transaction
as the domain rows it describes, so a committed mutation always has its record and a
rolled-back one never does. Only successful (2xx) answers are recorded: retrying a
refusal recomputes it, or succeeds once the cause (a missing budget, say) is gone.

The fingerprint is SHA-256 over the canonical JSON of the method, the concrete path and
the parsed body, so key order and whitespace in the caller's JSON do not matter. Nothing
here reads the clock: callers pass the instant.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .clock import require_aware

__all__ = [
    "IDEMPOTENCY_KEY_HEADER",
    "IDEMPOTENCY_KEY_INVALID",
    "IDEMPOTENCY_KEY_REUSED",
    "IDEMPOTENCY_TABLE_NAME",
    "MAX_IDEMPOTENCY_KEY_LENGTH",
    "IdempotencyKeyInvalidError",
    "IdempotencyKeyReusedError",
    "IdempotencyStore",
    "StoredResponse",
    "idempotency_table",
    "jsonable",
    "request_fingerprint",
]

IDEMPOTENCY_KEY_HEADER: Final[str] = "Idempotency-Key"
IDEMPOTENCY_TABLE_NAME: Final[str] = "idempotency_records"
IDEMPOTENCY_KEY_REUSED: Final[str] = "idempotency_key_reused"
IDEMPOTENCY_KEY_INVALID: Final[str] = "idempotency_key_invalid"
MAX_IDEMPOTENCY_KEY_LENGTH: Final[int] = 255


def idempotency_table(
    metadata: sa.MetaData, schema: str, *, name: str = IDEMPOTENCY_TABLE_NAME
) -> sa.Table:
    """The idempotency record table in the service's own ``schema``, on ``metadata``.

    The service's migration creates the same DDL (``plm_0035_idempotency_records``).
    """
    return sa.Table(
        name, metadata,
        sa.Column("idempotency_key", sa.String(MAX_IDEMPOTENCY_KEY_LENGTH), primary_key=True),
        sa.Column("request_method", sa.String(8), nullable=False),
        sa.Column("request_path", sa.String(512), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("response_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )


def request_fingerprint(method: str, path: str, body: Any) -> str:
    """SHA-256 of the canonical JSON of (method, path, body)."""
    canonical = json.dumps(
        {"method": method.upper(), "path": path, "body": body},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyKeyReusedError(RuntimeError):
    """The key was already used for a different request."""

    code: Final[str] = IDEMPOTENCY_KEY_REUSED

    def __init__(self, key: str, *, stored_path: str) -> None:
        self.key = key
        self.stored_path = stored_path
        super().__init__(
            f"Idempotency-Key {key!r} was already used for a different request "
            f"(first used on {stored_path}); a key names exactly one request"
        )


class IdempotencyKeyInvalidError(ValueError):
    """The header is present but blank or too long."""

    code: Final[str] = IDEMPOTENCY_KEY_INVALID


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: Any
    replayed: bool
    """``True`` when this answer was read back from a record, not freshly computed."""


def _normalise_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not key or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise IdempotencyKeyInvalidError(
            f"Idempotency-Key must be 1..{MAX_IDEMPOTENCY_KEY_LENGTH} non-blank characters"
        )
    return key


class IdempotencyStore:
    """Looks up, records and replays one service's idempotent responses."""

    def __init__(self, table: sa.Table) -> None:
        self._table = table

    @property
    def table(self) -> sa.Table:
        return self._table

    def lookup(
        self, session: Session, key: str, *, fingerprint: str
    ) -> StoredResponse | None:
        """The stored answer for ``key``; ``None`` if unused; refusal if used differently."""
        row = session.execute(
            sa.select(self._table).where(self._table.c.idempotency_key == key)
        ).mappings().one_or_none()
        if row is None:
            return None
        if row["request_fingerprint"] != fingerprint:
            raise IdempotencyKeyReusedError(key, stored_path=str(row["request_path"]))
        return StoredResponse(
            status_code=int(row["status_code"]), body=row["response_json"], replayed=True
        )

    def record(
        self,
        session: Session,
        key: str,
        *,
        method: str,
        path: str,
        fingerprint: str,
        status_code: int,
        body: Any,
        now: datetime,
    ) -> None:
        """Add the record to ``session``'s transaction; the caller commits."""
        session.execute(
            self._table.insert().values(
                idempotency_key=key,
                request_method=method.upper(),
                request_path=path[:512],
                request_fingerprint=fingerprint,
                status_code=status_code,
                response_json=body,
                created_at=require_aware(now).astimezone(UTC),
            )
        )

    def run(
        self,
        session: Session,
        raw_key: str | None,
        *,
        method: str,
        path: str,
        body: Any,
        now: Callable[[], datetime],
        operation: Callable[[], tuple[int, Any]],
    ) -> StoredResponse:
        """Replay, or run ``operation`` and record its answer, then commit.

        ``operation`` performs the mutation in ``session`` without committing and returns
        ``(status_code, json_body)``; it raises to refuse (nothing is recorded, and the
        caller rolls back). A non-2xx answer it *returns* is a refusal whose partial work
        must persist (for example, provider calls already paid for before a budget stop):
        it is committed but not recorded, so a retry with the key runs again. Without a
        key, ``operation`` runs and is committed unrecorded.
        A concurrent request with the same key that commits first makes this one's insert
        fail; the session is rolled back and the winner's answer is replayed (or refused
        as a reuse if the requests differ).
        """
        key = _normalise_key(raw_key)
        if key is None:
            status_code, response = operation()
            session.commit()
            return StoredResponse(status_code, response, replayed=False)
        fingerprint = request_fingerprint(method, path, body)
        stored = self.lookup(session, key, fingerprint=fingerprint)
        if stored is not None:
            session.rollback()
            return stored
        try:
            status_code, response = operation()
            if 200 <= status_code < 300:
                self.record(session, key, method=method, path=path, fingerprint=fingerprint,
                            status_code=status_code, body=response, now=now())
            session.commit()
        except IntegrityError:
            session.rollback()
            stored = self.lookup(session, key, fingerprint=fingerprint)
            if stored is None:
                raise
            return stored
        return StoredResponse(status_code, response, replayed=False)


def jsonable(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A JSON round trip, so a fresh answer and its replay are byte-for-byte the same."""
    loaded: dict[str, Any] = json.loads(json.dumps(payload, sort_keys=True, default=str))
    return loaded

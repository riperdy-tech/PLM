"""Correlation IDs as a context variable (W0.8).

Deliberately dependency-free (stdlib only): Temporal workflow and activity
modules import this without dragging logging, metrics or gRPC into the workflow
sandbox, and any process — worker, API, CLI, test — can carry an id.

Usage:

* inbound HTTP: :class:`CorrelationIdMiddleware` reads ``X-Correlation-ID`` (or
  mints one) and echoes it on the response; request handling runs inside
  :func:`correlation_scope`. FastAPI can also inject
  :func:`correlation_id_dependency`.
* Temporal activities: the workflow passes the id as an ordinary activity
  argument; the activity wraps its body in :func:`correlation_scope` so every
  log line it emits carries it (see packages/plm_platform/README.md).
* :func:`plm_platform.observability.configure_logging` attaches a filter that
  copies the current id into every record, so the rule "every log line emitted
  while a correlation ID is set must carry it" holds by construction.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Final

__all__ = [
    "CORRELATION_HEADER",
    "CorrelationIdMiddleware",
    "correlation_id_dependency",
    "correlation_scope",
    "current_correlation_id",
    "ensure_correlation_id",
    "new_correlation_id",
]

CORRELATION_HEADER: Final[str] = "X-Correlation-ID"
"""Header carrying the correlation id (matched case-insensitively on the wire)."""

_MAX_HEADER_LENGTH: Final[int] = 128

_correlation_id: ContextVar[str | None] = ContextVar("plm_correlation_id", default=None)

_Scope = dict[str, Any]
_Message = dict[str, Any]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]
_ASGIApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]


def new_correlation_id() -> str:
    """A fresh correlation id: 32 lowercase hex characters (uuid4)."""
    return uuid.uuid4().hex


def current_correlation_id() -> str | None:
    """The correlation id for the current context, or ``None`` when unset."""
    return _correlation_id.get()


def ensure_correlation_id() -> str:
    """The current correlation id, minting and installing one when absent."""
    existing = _correlation_id.get()
    if existing is not None:
        return existing
    minted = new_correlation_id()
    _correlation_id.set(minted)
    return minted


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Set the correlation id for the enclosed block (minted when ``None``).

    Restores the previous id on exit, including on exception. This is how a
    Temporal activity re-establishes the workflow's id around its body.
    """
    resolved = new_correlation_id() if correlation_id is None else correlation_id
    token: Token[str | None] = _correlation_id.set(resolved)
    try:
        yield resolved
    finally:
        _correlation_id.reset(token)


def correlation_id_dependency() -> str:
    """FastAPI-compatible dependency: ``Depends(correlation_id_dependency)``.

    A zero-argument callable, so no FastAPI import is needed here. When the
    middleware has already installed the header value it is returned; otherwise
    (e.g. the endpoint is mounted without the middleware) one is minted.
    """
    return ensure_correlation_id()


class CorrelationIdMiddleware:
    """Pure-ASGI middleware: read ``X-Correlation-ID`` or mint, echo it back.

    Works with any ASGI app; for FastAPI::

        app.add_middleware(CorrelationIdMiddleware)

    Downstream handling runs inside :func:`correlation_scope`, so every log line
    emitted while serving the request carries the id. Header values longer than
    128 characters are ignored (a fresh id is minted) to keep log volume bounded.
    """

    def __init__(self, app: _ASGIApp, header_name: str = CORRELATION_HEADER) -> None:
        self.app = app
        self.header_name = header_name
        self._header = header_name.lower().encode("latin-1")

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        correlation_id = self._incoming_id(scope) or new_correlation_id()
        token: Token[str | None] = _correlation_id.set(correlation_id)
        response_header = (self._header, correlation_id.encode("latin-1"))

        async def send_with_header(message: _Message) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [*message.get("headers", []), response_header],
                }
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _correlation_id.reset(token)

    def _incoming_id(self, scope: _Scope) -> str | None:
        for key, value in scope.get("headers", []):
            if key.lower() == self._header:
                decoded = value.decode("latin-1").strip()
                return decoded if 0 < len(decoded) <= _MAX_HEADER_LENGTH else None
        return None

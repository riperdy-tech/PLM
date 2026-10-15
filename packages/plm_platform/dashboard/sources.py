"""Where the live dashboard reads from: the database (read-only) and the phase APIs.

Every live view is built from *panels*. A panel either carries data
(``{"status": "ok", "source": ..., "data": ...}``) or says why it has none
(``{"status": "unavailable", "source": ..., "reason": ...}``). An unreachable database,
a missing table or a phase API that refuses or does not answer makes that panel
``unavailable``; it never turns into zeros, and the live dashboard never falls back to
the demo data (R5).

* :class:`ReadOnlyDatabase` runs a read in its own session, inside a ``READ ONLY``
  transaction on PostgreSQL, and always rolls back. The dashboard writes nothing through
  it; the only write path is :class:`~plm_platform.exceptions.ExceptionQueue` in
  :mod:`.actions`.
* :class:`PhaseApis` calls a phase API with the platform service token. A missing token,
  a connection failure or a non-2xx answer raises :class:`SourceUnavailable` naming the
  phase and the ErrorShape code the service returned.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Final, TypeVar

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import SERVICE_TOKEN_HEADER, SERVICE_TOKEN_VAR
from ..temporal import Phase

__all__ = [
    "LIVE_DATA_SOURCE",
    "PhaseApis",
    "ReadOnlyDatabase",
    "SourceUnavailable",
    "panel_ok",
    "panel_unavailable",
]

LIVE_DATA_SOURCE: Final[str] = "live"
"""Provenance label of every payload the live dashboard serves."""

_REASON_LIMIT: Final[int] = 300

T = TypeVar("T")


class SourceUnavailable(Exception):
    """A source could not answer. ``reason`` is shown to the operator verbatim."""

    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason[:_REASON_LIMIT]


def panel_ok(source: str, data: Any) -> dict[str, Any]:
    return {"status": "ok", "source": source, "data": data}


def panel_unavailable(source: str, reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "source": source, "reason": reason[:_REASON_LIMIT]}


def _first_line(exc: BaseException) -> str:
    text = str(getattr(exc, "orig", None) or exc).strip()
    return (text.splitlines() or [type(exc).__name__])[0]


class ReadOnlyDatabase:
    """Read-only sessions over the platform database.

    ``session_factory=None`` means the database is not configured; every read raises
    :class:`SourceUnavailable` with ``missing_reason``.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | None,
        *,
        missing_reason: str = "database not configured (DATABASE_URL)",
    ) -> None:
        self._factory = session_factory
        self._missing_reason = missing_reason

    @property
    def configured(self) -> bool:
        return self._factory is not None

    @contextmanager
    def session(self, source: str) -> Iterator[Session]:
        if self._factory is None:
            raise SourceUnavailable(source, self._missing_reason)
        session = self._factory()
        try:
            if session.get_bind().dialect.name == "postgresql":
                session.connection().exec_driver_sql("SET TRANSACTION READ ONLY")
            yield session
        except SQLAlchemyError as exc:
            raise SourceUnavailable(source, _first_line(exc)) from exc
        finally:
            session.rollback()
            session.close()

    def read(self, source: str, query: Callable[[Session], T]) -> T:
        """Run ``query`` in a read-only session; any database error becomes unavailable."""
        with self.session(source) as session:
            return query(session)


class PhaseApis:
    """GET-only client for the phase APIs (service token on every call)."""

    def __init__(
        self,
        urls: Mapping[Phase, str],
        *,
        token: str | None,
        transport: httpx.BaseTransport | None = None,
        clients: Mapping[Phase, httpx.Client] | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        """``clients`` supplies a ready client per phase (e.g. an in-process app in tests);
        every other phase gets a fresh client on its base URL (``transport`` optional)."""
        self._urls = dict(urls)
        self._token = token
        self._transport = transport
        self._clients = dict(clients or {})
        self._timeout = timeout_seconds

    @staticmethod
    def source(phase: Phase) -> str:
        return f"api:{phase.name.lower()}"

    def get(self, phase: Phase, path: str, params: Mapping[str, Any] | None = None
            ) -> httpx.Response:
        """One GET; raises :class:`SourceUnavailable` when the call cannot be made."""
        source = self.source(phase)
        if self._token is None:
            raise SourceUnavailable(
                source, f"{SERVICE_TOKEN_VAR} is not set: the phase APIs refuse every call")
        headers = {SERVICE_TOKEN_HEADER: self._token}
        injected = self._clients.get(phase)
        base = self._urls.get(phase, "") if injected is None else str(injected.base_url)
        try:
            if injected is not None:
                return injected.get(path, params=dict(params or {}), headers=headers)
            if not base:
                raise SourceUnavailable(source, "no base URL configured")
            with httpx.Client(base_url=base, timeout=self._timeout,
                              transport=self._transport) as client:
                return client.get(path, params=dict(params or {}), headers=headers)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(source, f"{base}{path}: {type(exc).__name__}: {exc}"
                                    ) from exc

    def get_json(self, phase: Phase, path: str, params: Mapping[str, Any] | None = None
                 ) -> Any:
        """The JSON body of a 2xx answer; anything else raises :class:`SourceUnavailable`."""
        response = self.get(phase, path, params)
        if response.status_code // 100 != 2:
            raise SourceUnavailable(self.source(phase), describe_refusal(path, response))
        try:
            return response.json()
        except ValueError as exc:
            raise SourceUnavailable(self.source(phase), f"{path}: answer is not JSON") from exc


def describe_refusal(path: str, response: httpx.Response) -> str:
    """``<path>: HTTP <status> <code>: <message>`` from an ErrorShape body when present."""
    code, message = error_code(response)
    return f"{path}: HTTP {response.status_code} {code}: {message}"


def error_code(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "http_error", response.text[:120]
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict) and "code" in detail:
        return str(detail["code"]), str(detail.get("message", ""))
    return "http_error", str(detail)[:120]

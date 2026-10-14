"""Mock HTTP utilities (``plm_platform.testing.http``): exercise a connector without the network.

Every phase that talks to an HTTP provider (P1's YouTube connector, P7's
YouTube client, P8's distributor, P9's connectors) tests against
:class:`httpx.MockTransport` driven by :class:`MockHTTP`. The mock:

* **records every request** (method, URL, parsed query, headers, body) so a
  test can assert on what the connector actually sent;
* **serves a scripted sequence** of canned outcomes — JSON bodies, error
  statuses, 429 with ``Retry-After``, 500s, timeouts and malformed bodies —
  consumed in order, so multi-call flows (pagination, retry loops) are explicit.

Two usage styles::

    # 1. hand the transport to a connector that accepts a client (preferred)
    mock = MockHTTP()
    mock.enqueue_json({"items": []})
    client = httpx.Client(transport=mock.transport, base_url="https://api.example")
    connector = YouTubeConnector(client=client)
    connector.fetch_videos(part="snippet")

    # 2. patch every httpx client the module constructs itself
    mock = MockHTTP()
    mock.install(monkeypatch)

    # either way, assert against the recording afterwards:
    mock.assert_query_params(0, part="snippet", maxResults="5")

Simulating failures takes one call::

    mock.enqueue_rate_limit(retry_after="30")   # 429 + Retry-After: 30
    mock.enqueue_server_error()                 # 500
    mock.enqueue_timeout()                      # raises httpx.ReadTimeout
    mock.enqueue_malformed_json()               # 200 whose body will not parse

The canned bodies are deliberately generic (``{"error": ...}``); connectors
must key off the status and headers, not off a provider's private error
vocabulary, and tests should assert the same way. When the sequence runs dry
the next request raises :class:`AssertionError` naming the request — pass
``repeat_last=True`` (or re-enqueue) if a flow legitimately calls the same
endpoint an unbounded number of times.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Self

import httpx

if TYPE_CHECKING:
    import pytest

TimeoutKind = Literal["connect", "read", "write", "pool"]
QueryValue = str | int | Sequence[str | int]

_TIMEOUT_EXCEPTIONS: Mapping[str, type[httpx.TimeoutException]] = {
    "connect": httpx.ConnectTimeout,
    "read": httpx.ReadTimeout,
    "write": httpx.WriteTimeout,
    "pool": httpx.PoolTimeout,
}

_DEFAULT_MALFORMED_JSON = b'{"items": ['

DEFAULT_RATE_LIMIT_BODY: Mapping[str, Any] = {"error": "rate_limit_exceeded"}
DEFAULT_SERVER_ERROR_BODY: Mapping[str, Any] = {"error": "internal_server_error"}


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One request exactly as the client sent it."""

    method: str
    url: str
    path: str
    query: Mapping[str, tuple[str, ...]]
    headers: httpx.Headers
    content: bytes

    def param(self, name: str) -> str | None:
        """The first value of a query parameter, or ``None`` when absent."""
        values = self.query.get(name)
        return values[0] if values else None

    def json(self) -> Any:
        """The request body parsed as JSON (raises when absent or not JSON)."""
        import json

        return json.loads(self.content)


@dataclass(frozen=True, slots=True)
class _Canned:
    """One scripted outcome: a response to return, or an exception to raise."""

    response: httpx.Response | None = None
    exception: Exception | None = None
    label: str = "response"


class MockHTTP:
    """A recording, scriptable transport for ``httpx`` clients.

    Create one per test (the ``mock_http`` fixture does), enqueue outcomes in
    call order with the ``enqueue_*`` methods (chainable), then assert against
    :attr:`requests`. See the module docstring for the full workflow.
    """

    def __init__(self, *, repeat_last: bool = False) -> None:
        self._queue: list[_Canned] = []
        self._requests: list[RecordedRequest] = []
        self._last_served: _Canned | None = None
        self._repeat_last = repeat_last
        self._transport = httpx.MockTransport(self._handle)

    # -- enqueueing ---------------------------------------------------------

    def enqueue_response(self, response: httpx.Response) -> Self:
        """Serve a fully-formed response (escape hatch for unusual cases)."""
        self._queue.append(_Canned(response=response))
        return self

    def enqueue_json(
        self,
        payload: Any,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> Self:
        """Serve ``payload`` as a JSON body with the given status."""
        response = httpx.Response(status, json=payload, headers=dict(headers or {}))
        self._queue.append(_Canned(response=response, label=f"json {status}"))
        return self

    def enqueue_text(
        self,
        text: str,
        *,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
        headers: Mapping[str, str] | None = None,
    ) -> Self:
        """Serve a plain-text body (e.g. an RSS/XML feed)."""
        all_headers = {"Content-Type": content_type, **dict(headers or {})}
        response = httpx.Response(status, text=text, headers=all_headers)
        self._queue.append(_Canned(response=response, label=f"text {status}"))
        return self

    def enqueue_status(
        self,
        status: int,
        *,
        payload: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Self:
        """Serve an arbitrary status; a JSON ``payload`` when one is given."""
        if payload is None:
            response = httpx.Response(status, headers=dict(headers or {}))
        else:
            response = httpx.Response(status, json=payload, headers=dict(headers or {}))
        self._queue.append(_Canned(response=response, label=f"status {status}"))
        return self

    def enqueue_rate_limit(
        self,
        *,
        retry_after: str = "60",
        status: int = 429,
        payload: Any = DEFAULT_RATE_LIMIT_BODY,
    ) -> Self:
        """Serve ``status`` (429 by default) with a ``Retry-After`` header."""
        return self.enqueue_status(status, payload=payload, headers={"Retry-After": retry_after})

    def enqueue_server_error(
        self,
        *,
        status: int = 500,
        payload: Any = DEFAULT_SERVER_ERROR_BODY,
    ) -> Self:
        """Serve ``status`` (500 by default) with a generic error body."""
        return self.enqueue_status(status, payload=payload)

    def enqueue_timeout(self, *, kind: TimeoutKind = "read", message: str | None = None) -> Self:
        """Make the request raise the ``httpx`` timeout exception a real client would see."""
        exception_type = _TIMEOUT_EXCEPTIONS[kind]
        text = message or f"simulated {kind} timeout"
        self._queue.append(_Canned(exception=exception_type(text), label=f"timeout {kind}"))
        return self

    def enqueue_malformed_json(
        self,
        *,
        content: bytes | None = None,
        status: int = 200,
    ) -> Self:
        """Serve a 200 whose body claims to be JSON but does not parse.

        The consumer-side failure to exercise is the *parse*: ``response.json()``
        raises ``json.JSONDecodeError`` and the connector must surface it as its
        own contract error rather than crashing with the raw exception.
        """
        body = _DEFAULT_MALFORMED_JSON if content is None else content
        response = httpx.Response(
            status, content=body, headers={"Content-Type": "application/json"}
        )
        self._queue.append(_Canned(response=response, label=f"malformed json {status}"))
        return self

    # -- wiring -------------------------------------------------------------

    @property
    def transport(self) -> httpx.MockTransport:
        """The transport to hand to ``httpx.Client``/``httpx.AsyncClient``."""
        return self._transport

    def client(self, **options: Any) -> httpx.Client:
        """A new ``httpx.Client`` bound to this mock."""
        options.setdefault("transport", self._transport)
        return httpx.Client(**options)

    def async_client(self, **options: Any) -> httpx.AsyncClient:
        """A new ``httpx.AsyncClient`` bound to this mock."""
        options.setdefault("transport", self._transport)
        return httpx.AsyncClient(**options)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Route ``httpx.Client()``/``httpx.AsyncClient()`` constructed after this call.

        Use this when the code under test builds its own client internally and
        cannot be handed one. Limitation: it patches the ``httpx`` package
        attributes, so modules that did ``from httpx import Client`` *before*
        installation keep the original class — prefer handing the transport in
        wherever the connector allows it. The patch is undone by ``monkeypatch``.
        """
        transport = self._transport
        real_client = httpx.Client
        real_async_client = httpx.AsyncClient

        def mock_client(**options: Any) -> httpx.Client:
            options.setdefault("transport", transport)
            return real_client(**options)

        def mock_async_client(**options: Any) -> httpx.AsyncClient:
            options.setdefault("transport", transport)
            return real_async_client(**options)

        monkeypatch.setattr(httpx, "Client", mock_client)
        monkeypatch.setattr(httpx, "AsyncClient", mock_async_client)

    # -- recording ----------------------------------------------------------

    @property
    def requests(self) -> tuple[RecordedRequest, ...]:
        """Every request served, in order."""
        return tuple(self._requests)

    @property
    def request_count(self) -> int:
        """How many requests the connector made."""
        return len(self._requests)

    def request(self, index: int = -1) -> RecordedRequest:
        """The request at ``index`` (default: the most recent one)."""
        if not self._requests:
            raise AssertionError("MockHTTP: no requests were made")
        try:
            return self._requests[index]
        except IndexError:
            raise AssertionError(
                f"MockHTTP: request #{index} does not exist "
                f"(only {len(self._requests)} request(s) recorded)"
            ) from None

    def assert_request(self, index: int, *, method: str | None = None, path: str | None = None) -> None:
        """Assert the method and/or path of the request at ``index``."""
        recorded = self.request(index)
        problems = []
        if method is not None and recorded.method != method:
            problems.append(f"  method: expected {method!r}, got {recorded.method!r}")
        if path is not None and recorded.path != path:
            problems.append(f"  path: expected {path!r}, got {recorded.path!r}")
        if problems:
            raise AssertionError(
                f"MockHTTP: request #{index} did not match ({recorded.method} {recorded.url}):\n"
                + "\n".join(problems)
            )

    def assert_query_params(self, index: int, /, **expected: QueryValue) -> None:
        """Assert query parameters of the request at ``index`` (exact values).

        Parameter names are keyword arguments::

            mock.assert_query_params(0, part="snippet", maxResults="5")

        Only the named parameters are compared; other parameters on the request
        are ignored. A parameter that appears multiple times must be given as a
        sequence and must match all values, in order.
        """
        recorded = self.request(index)
        problems = []
        for name, value in expected.items():
            wanted = (value,) if isinstance(value, (str, int)) else tuple(value)
            wanted_text = tuple(str(item) for item in wanted)
            got = recorded.query.get(name, ())
            if got != wanted_text:
                problems.append(f"  {name}: expected {list(wanted_text)!r}, got {list(got)!r}")
        if problems:
            raise AssertionError(
                f"MockHTTP: query parameters of request #{index} did not match "
                f"({recorded.method} {recorded.path}):\n" + "\n".join(problems)
            )

    # -- transport internals ------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self._requests.append(self._record(request))
        canned = self._next()
        if canned.exception is not None:
            raise canned.exception
        assert canned.response is not None  # _Canned always carries one of the two
        return canned.response

    def _next(self) -> _Canned:
        if self._queue:
            self._last_served = self._queue.pop(0)
            return self._last_served
        if self._repeat_last and self._last_served is not None:
            return self._last_served
        request = self._requests[-1]
        raise AssertionError(
            f"MockHTTP: no canned responses left for request "
            f"#{len(self._requests)} ({request.method} {request.url}): "
            "enqueue another response, or create the mock with repeat_last=True"
        )

    @staticmethod
    def _record(request: httpx.Request) -> RecordedRequest:
        query: dict[str, list[str]] = {}
        for name, value in request.url.params.multi_items():
            query.setdefault(name, []).append(value)
        return RecordedRequest(
            method=request.method,
            url=str(request.url),
            path=request.url.path,
            query={name: tuple(values) for name, values in query.items()},
            headers=request.headers,
            content=request.read(),
        )

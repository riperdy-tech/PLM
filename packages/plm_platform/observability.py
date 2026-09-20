"""Structured logging, correlation, metrics and per-run cost counters (W0.8).

Four rules this module exists to enforce (P0 §2 "Cost is a first-class metric"):

* **One JSON object per log line.** :func:`configure_logging` installs a handler
  whose formatter emits timestamp, level, logger, message, the bound
  ``correlation_id``/``run_id`` (when set) and any structured extras. Nothing in
  ``plm_platform`` uses a bare ``print`` — a unit test enforces it.
* **Correlation ids travel in a context variable** (:mod:`plm_platform.correlation`,
  re-exported here). Every log line emitted while one is set carries it; the
  Temporal-workflow convention is documented in ``packages/plm_platform/README.md``.
* **Redaction by construction.** A :class:`plm_platform.config.Secret` renders as
  ``**********`` through ``str``/``format``/``repr``, and the JSON encoder falls
  back to ``str()`` for unknown types — so a secret passed as a ``%s`` argument,
  embedded in a dict, or supplied via ``extra=`` never reaches the output.
* **Metrics and budgets.** Prometheus instruments live on a dedicated
  :data:`METRICS_REGISTRY` served by :class:`MetricsApp`/`metrics_payload`;
  :class:`CostTracker` keeps the five per-run counters
  (:class:`CostMetric`) that a phase reads via :meth:`CostTracker.totals` to
  enforce a budget ceiling.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, TextIO

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from .correlation import (
    CORRELATION_HEADER,
    CorrelationIdMiddleware,
    correlation_id_dependency,
    correlation_scope,
    current_correlation_id,
    ensure_correlation_id,
    new_correlation_id,
)

__all__ = [
    "CORRELATION_HEADER",
    "COST_TRACKER",
    "METRICS_PATH",
    "METRICS_REGISTRY",
    "OPERATIONS_TOTAL",
    "OPERATION_SECONDS",
    "CorrelationIdMiddleware",
    "CostMetric",
    "CostTracker",
    "JSONFormatter",
    "MetricsApp",
    "RunCosts",
    "RunTotals",
    "UnknownRunError",
    "configure_logging",
    "correlation_id_dependency",
    "correlation_scope",
    "current_correlation_id",
    "ensure_correlation_id",
    "get_or_create_counter",
    "metrics_payload",
    "new_correlation_id",
    "run_scope",
    "track_operation",
]

logger = logging.getLogger(__name__)

_ReservedAttrs = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "correlation_id", "run_id"}

_run_id: ContextVar[str | None] = ContextVar("plm_run_id", default=None)


class JSONFormatter(logging.Formatter):
    """Render each record as one JSON object; unknown values fall back to ``str``.

    The ``default=str`` fallback is what makes redaction hold for structured
    extras: a :class:`~plm_platform.config.Secret` there stringifies to the
    redaction marker instead of raising.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id is not None:
            payload["correlation_id"] = correlation_id
        run_id = getattr(record, "run_id", None)
        if run_id is not None:
            payload["run_id"] = run_id
        for key, value in record.__dict__.items():
            if key in _ReservedAttrs or key.startswith("_"):
                continue
            payload.setdefault(key, value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class _ContextFilter(logging.Filter):
    """Stamp the current correlation/run id onto every record passing through."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "correlation_id", None) is None:
            record.correlation_id = current_correlation_id()
        if getattr(record, "run_id", None) is None:
            record.run_id = _run_id.get()
        return True


class _JSONStreamHandler(logging.StreamHandler[TextIO]):
    """StreamHandler carrying the platform JSON formatter (and the removal marker)."""


def configure_logging(*, level: str | int = "INFO", stream: TextIO | None = None) -> None:
    """Install the platform JSON handler on the root logger (idempotent).

    Call once per process after settings load. Re-running removes the handler a
    previous call installed and installs a fresh one; other handlers are left
    untouched.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _JSONStreamHandler):
            root.removeHandler(handler)
    handler = _JSONStreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
    root.setLevel(level)


METRICS_REGISTRY: Final[CollectorRegistry] = CollectorRegistry()
"""Dedicated registry: scraping the platform never mixes in process-global metrics."""

_COUNTERS_BY_NAME: dict[str, Counter] = {}


def get_or_create_counter(
    name: str, documentation: str, labelnames: Sequence[str] = (),
) -> Counter:
    """Get registry counter `name`, creating and registering it on first use.

    Service packages are importable under two module identities — their
    ``services.<package>`` path and the editable top-level ``<package>`` mapping —
    so a module-level ``Counter(...)`` runs twice against the shared
    :data:`METRICS_REGISTRY`, and the second call raises ``DuplicateTimeseries``:
    an in-process CLI invocation (the Typer app) crashes where a fresh process
    would not. Both calls refer to the same intended instrument, so the already
    registered collector is returned; a name is still owned by one collector, and
    a same-named request from *different* instrumentation remains a collision the
    registry refuses.
    """
    existing = _COUNTERS_BY_NAME.get(name)
    if existing is not None:
        return existing
    counter = Counter(name, documentation, labelnames, registry=METRICS_REGISTRY)
    _COUNTERS_BY_NAME[name] = counter
    return counter


METRICS_PATH: Final[str] = "/metrics"
"""Path :class:`MetricsApp` serves the Prometheus text format on."""

OPERATIONS_TOTAL: Final[Counter] = Counter(
    "plm_operations_total",
    "Completed platform operations.",
    ["operation", "outcome"],
    registry=METRICS_REGISTRY,
)
"""Counter of finished operations by outcome (``ok``/``error``)."""

OPERATION_SECONDS: Final[Histogram] = Histogram(
    "plm_operation_seconds",
    "Wall-clock duration of platform operations.",
    ["operation"],
    registry=METRICS_REGISTRY,
)
"""Histogram of operation durations in seconds."""


@contextmanager
def track_operation(operation: str) -> Iterator[None]:
    """Time an operation, recording its outcome (``ok``/``error``) and duration."""
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        OPERATION_SECONDS.labels(operation=operation).observe(time.perf_counter() - started)
        OPERATIONS_TOTAL.labels(operation=operation, outcome=outcome).inc()


def metrics_payload(registry: CollectorRegistry | None = None) -> bytes:
    """The Prometheus text exposition a plain HTTP handler can return verbatim."""
    return generate_latest(METRICS_REGISTRY if registry is None else registry)


class MetricsApp:
    """Minimal ASGI app: ``GET /metrics`` returns the registry in text format.

    Mount it directly (``uvicorn module:MetricsApp``-style wrapper) or beside a
    FastAPI app; wrong path returns 404, wrong method 405.
    """

    def __init__(
        self,
        *,
        path: str = METRICS_PATH,
        registry: CollectorRegistry | None = None,
    ) -> None:
        self.path = path
        self.registry = METRICS_REGISTRY if registry is None else registry

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope["type"] != "http":
            return
        if scope.get("path") != self.path:
            await _send_status(send, 404)
            return
        if scope.get("method") != "GET":
            await _send_status(send, 405)
            return
        payload = metrics_payload(self.registry)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", CONTENT_TYPE_LATEST.encode("latin-1")),
                    (b"content-length", str(len(payload)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


_Scope = dict[str, Any]
_Message = dict[str, Any]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]


async def _send_status(send: _Send, status: int) -> None:
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": b""})


class CostMetric(StrEnum):
    """The five first-class per-run counters (P0 §2)."""

    API_CALLS = "api_calls"
    LLM_TOKENS = "llm_tokens"
    LLM_COST_ESTIMATE = "llm_cost_estimate"
    STORAGE_BYTES = "storage_bytes"
    RENDER_SECONDS = "render_seconds"


_COUNT_METRICS: Final[frozenset[CostMetric]] = frozenset(
    {CostMetric.API_CALLS, CostMetric.LLM_TOKENS, CostMetric.STORAGE_BYTES}
)


@dataclass(slots=True)
class RunCosts:
    """Mutable accumulator of one run's counters; read snapshots, never share it."""

    api_calls: int = 0
    llm_tokens: int = 0
    llm_cost_estimate: float = 0.0
    storage_bytes: int = 0
    render_seconds: float = 0.0

    def add(self, metric: CostMetric, amount: float) -> None:
        """Add ``amount`` to ``metric``; rejects negatives and fractional counts."""
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise TypeError(f"{metric.value} expects a number, got {amount!r}")
        if amount < 0:
            raise ValueError(f"{metric.value} cannot decrease, got {amount!r}")
        if metric in _COUNT_METRICS and not isinstance(amount, int):
            raise ValueError(f"{metric.value} counts whole units, got {amount!r}")
        setattr(self, metric.value, getattr(self, metric.value) + amount)

    def get(self, metric: CostMetric) -> float:
        """Current value of ``metric``."""
        return float(getattr(self, metric.value))

    def snapshot(self) -> RunCosts:
        """A detached copy — a reader can never mutate the tracker's state."""
        return replace(self)

    def as_dict(self) -> dict[str, float]:
        """All counters keyed by metric name, ready for a log record."""
        return {metric.value: self.get(metric) for metric in CostMetric}


@dataclass(frozen=True, slots=True)
class RunTotals:
    """Immutable view of one run: identity, window and a counter snapshot."""

    run_id: str
    started_at: datetime
    finished_at: datetime | None
    costs: RunCosts

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock duration once finished, ``None`` while running."""
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class UnknownRunError(KeyError):
    """A cost operation named a run id the tracker has never seen."""


class CostTracker:
    """Per-run cost counters keyed by run identifier.

    A run begins with :meth:`begin_run` (or :func:`run_scope`), counters move via
    :meth:`record`, and a phase reads :meth:`totals` to compare against a budget
    ceiling before spending more. Totals are snapshots: mutating one cannot
    change the tracker.
    """

    def __init__(self) -> None:
        self._runs: dict[str, RunTotals] = {}
        self._lock = threading.Lock()

    def begin_run(self, run_id: str, *, started_at: datetime | None = None) -> None:
        """Start tracking ``run_id``; duplicate ids are a bug and are rejected."""
        with self._lock:
            if run_id in self._runs:
                raise ValueError(f"run {run_id!r} is already tracked")
            self._runs[run_id] = RunTotals(
                run_id=run_id,
                started_at=datetime.now(UTC) if started_at is None else started_at,
                finished_at=None,
                costs=RunCosts(),
            )

    def record(self, run_id: str, metric: CostMetric, amount: float) -> None:
        """Add ``amount`` to ``metric`` for ``run_id``."""
        with self._lock:
            totals = self._require(run_id)
            if totals.finished_at is not None:
                raise ValueError(f"run {run_id!r} is already finished")
            totals.costs.add(metric, amount)

    def finish_run(self, run_id: str) -> RunTotals:
        """Close the run's window (idempotent) and return its totals."""
        with self._lock:
            totals = self._require(run_id)
            if totals.finished_at is None:
                totals = replace(totals, finished_at=datetime.now(UTC))
                self._runs[run_id] = totals
            return replace(totals, costs=totals.costs.snapshot())

    def totals(self, run_id: str) -> RunTotals:
        """Read the run's window and counters (this is how a budget is enforced)."""
        with self._lock:
            totals = self._require(run_id)
            return replace(totals, costs=totals.costs.snapshot())

    def _require(self, run_id: str) -> RunTotals:
        try:
            return self._runs[run_id]
        except KeyError:
            raise UnknownRunError(run_id) from None


COST_TRACKER: Final[CostTracker] = CostTracker()
"""Process-wide tracker; phases may build their own :class:`CostTracker` instead."""


@contextmanager
def run_scope(run_id: str, *, tracker: CostTracker | None = None) -> Iterator[str]:
    """Track one run's window and counters, logging start and finish lines.

    The finish line is the canonical "one correlated line showing the run's cost
    counters": it carries ``correlation_id`` (from the enclosing
    :func:`~plm_platform.correlation.correlation_scope`), ``run_id`` and the
    counter snapshot.
    """
    active = COST_TRACKER if tracker is None else tracker
    active.begin_run(run_id)
    token: Token[str | None] = _run_id.set(run_id)
    logger.info("run started", extra={"run_id": run_id})
    try:
        yield run_id
    finally:
        totals = active.finish_run(run_id)
        logger.info(
            "run finished",
            extra={
                "run_id": run_id,
                "costs": totals.costs.as_dict(),
                "duration_seconds": totals.duration_seconds,
            },
        )
        _run_id.reset(token)

"""W0.8 unit tests: JSON logging, correlation ids, metrics and run cost counters.

Everything here is hermetic: logs are captured into an in-memory stream, HTTP is
exercised through httpx's ASGI transport, and no Temporal/MinIO/Postgres server
is involved.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from pathlib import Path

import httpx
import pytest
from plm_platform.config import Secret
from plm_platform.observability import (
    CORRELATION_HEADER,
    METRICS_PATH,
    OPERATION_SECONDS,
    OPERATIONS_TOTAL,
    CorrelationIdMiddleware,
    CostMetric,
    CostTracker,
    MetricsApp,
    UnknownRunError,
    configure_logging,
    correlation_id_dependency,
    correlation_scope,
    current_correlation_id,
    ensure_correlation_id,
    metrics_payload,
    run_scope,
    track_operation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_PACKAGE = REPO_ROOT / "packages" / "plm_platform"
HEX_32 = re.compile(r"^[0-9a-f]{32}$")


def capture_logs(level: str = "DEBUG") -> io.StringIO:
    buffer = io.StringIO()
    configure_logging(level=level, stream=buffer)
    return buffer


def log_lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


def test_log_lines_are_single_line_json_with_timestamp_and_context() -> None:
    buffer = capture_logs()
    with correlation_scope("corr-1"):
        logging.getLogger("plm.test").info("hello %s", "world")
    lines = buffer.getvalue().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["level"] == "INFO"
    assert record["logger"] == "plm.test"
    assert record["message"] == "hello world"
    assert record["correlation_id"] == "corr-1"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", str(record["timestamp"]))


def test_correlation_id_is_omitted_when_not_set() -> None:
    buffer = capture_logs()
    logging.getLogger("plm.test").info("outside")
    record = log_lines(buffer)[0]
    assert "correlation_id" not in record


def test_correlation_scope_mints_and_restores() -> None:
    assert current_correlation_id() is None
    with correlation_scope() as minted:
        assert HEX_32.match(minted)
        assert current_correlation_id() == minted
        with correlation_scope("inner") as inner:
            assert inner == "inner"
            assert current_correlation_id() == "inner"
        assert current_correlation_id() == minted
    assert current_correlation_id() is None


@pytest.mark.asyncio
async def test_correlation_survives_awaits_and_the_dependency_returns_it() -> None:
    assert ensure_correlation_id() is not None
    with correlation_scope("cid"):
        await asyncio.sleep(0)
        assert correlation_id_dependency() == "cid"
        assert current_correlation_id() == "cid"

@pytest.mark.asyncio

async def test_middleware_reads_the_header_and_echoes_it() -> None:
    seen: list[str | None] = []

    async def app(scope: object, receive: object, send: object) -> None:  # type: ignore[type-arg]
        seen.append(current_correlation_id())
        await send(  # type: ignore[misc]
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"ok"})  # type: ignore[misc]

    transport = httpx.ASGITransport(app=CorrelationIdMiddleware(app))
    async with httpx.AsyncClient(transport=transport, base_url="http://plm.test") as client:
        response = await client.get("/", headers={CORRELATION_HEADER: "incoming-1"})
        minted = (await client.get("/")).headers[CORRELATION_HEADER.lower()]
    assert response.headers[CORRELATION_HEADER.lower()] == "incoming-1"
    assert seen[0] == "incoming-1"
    assert HEX_32.match(minted)
    assert seen[1] == minted
    assert current_correlation_id() is None


def test_secret_is_redacted_even_as_a_format_argument() -> None:
    buffer = capture_logs()
    secret = Secret("super-secret-value")
    logger = logging.getLogger("plm.secrets")
    with correlation_scope("corr-secret"):
        logger.info("connecting with token=%s", secret)
        logger.info("payload %s", {"api_key": secret})
        logger.info("structured", extra={"api_key": secret})
    raw = buffer.getvalue()
    assert "super-secret-value" not in raw
    records = log_lines(buffer)
    assert records[0]["message"] == "connecting with token=**********"
    assert "super-secret-value" not in json.dumps(records)
    assert records[0]["correlation_id"] == "corr-secret"
    assert records[2]["api_key"] == "**********"


def test_metrics_counter_and_histogram_are_recorded() -> None:
    with track_operation("unit-ok"):
        pass
    with pytest.raises(RuntimeError, match="boom"), track_operation("unit-fail"):
        raise RuntimeError("boom")
    payload = metrics_payload().decode("utf-8")
    assert 'plm_operations_total{operation="unit-ok",outcome="ok"}' in payload
    assert 'plm_operations_total{operation="unit-fail",outcome="error"}' in payload
    assert 'plm_operation_seconds_count{operation="unit-ok"}' in payload
    assert OPERATIONS_TOTAL.labels(operation="unit-ok", outcome="ok")._value.get() >= 1
    assert OPERATION_SECONDS.labels(operation="unit-ok")._sum.get() >= 0

@pytest.mark.asyncio

async def test_metrics_app_serves_the_registry_over_http() -> None:
    transport = httpx.ASGITransport(app=MetricsApp())
    async with httpx.AsyncClient(transport=transport, base_url="http://plm.test") as client:
        response = await client.get(METRICS_PATH)
        missing = await client.get("/not-metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "plm_operations_total" in response.text
    assert missing.status_code == 404


def test_run_cost_counters_accumulate_and_are_readable_for_a_budget() -> None:
    tracker = CostTracker()
    tracker.begin_run("run-1")
    tracker.record("run-1", CostMetric.API_CALLS, 3)
    tracker.record("run-1", CostMetric.LLM_TOKENS, 1200)
    tracker.record("run-1", CostMetric.LLM_COST_ESTIMATE, 0.42)
    tracker.record("run-1", CostMetric.STORAGE_BYTES, 4096)
    tracker.record("run-1", CostMetric.RENDER_SECONDS, 12.5)
    totals = tracker.totals("run-1")
    assert totals.costs.api_calls == 3
    assert totals.costs.llm_tokens == 1200
    assert totals.costs.llm_cost_estimate == pytest.approx(0.42)
    assert totals.costs.storage_bytes == 4096
    assert totals.costs.render_seconds == pytest.approx(12.5)
    assert totals.finished_at is None
    # A phase enforces a ceiling by reading totals(); the read must not mutate state.
    assert tracker.totals("run-1").costs.llm_cost_estimate > 0.25
    assert tracker.totals("run-1").costs is not totals.costs
    finished = tracker.finish_run("run-1")
    assert finished.finished_at is not None
    assert finished.duration_seconds is not None and finished.duration_seconds >= 0


def test_run_cost_tracker_rejects_unknown_runs_and_bad_values() -> None:
    tracker = CostTracker()
    tracker.begin_run("run-2")
    with pytest.raises(UnknownRunError):
        tracker.record("missing", CostMetric.API_CALLS, 1)
    with pytest.raises(UnknownRunError):
        tracker.totals("missing")
    with pytest.raises(ValueError):
        tracker.record("run-2", CostMetric.API_CALLS, -1)
    with pytest.raises(ValueError):
        tracker.record("run-2", CostMetric.LLM_TOKENS, 1.5)
    with pytest.raises(ValueError):
        tracker.record("run-2", CostMetric.RENDER_SECONDS, -0.5)
    with pytest.raises(TypeError):
        tracker.record("run-2", CostMetric.API_CALLS, "three")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        tracker.begin_run("run-2")


def test_run_scope_logs_correlated_json_with_the_counters() -> None:
    buffer = capture_logs()
    tracker = CostTracker()
    with correlation_scope("corr-run"), run_scope("run-log", tracker=tracker) as run_id:
        tracker.record(run_id, CostMetric.API_CALLS, 2)
        tracker.record(run_id, CostMetric.LLM_COST_ESTIMATE, 0.01)
    records = log_lines(buffer)
    started = next(record for record in records if record["message"] == "run started")
    finished = next(record for record in records if record["message"] == "run finished")
    for record in (started, finished):
        assert record["correlation_id"] == "corr-run"
        assert record["run_id"] == "run-log"
    assert finished["costs"]["api_calls"] == 2
    assert finished["costs"]["llm_cost_estimate"] == pytest.approx(0.01)
    assert tracker.totals("run-log").finished_at is not None


def test_platform_package_never_uses_a_bare_print() -> None:
    bare_print = re.compile(r"^\s*print\s*\(", re.MULTILINE)
    offenders = [
        path.name
        for path in sorted(PLATFORM_PACKAGE.glob("*.py"))
        if bare_print.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []

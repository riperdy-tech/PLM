# plm_platform

Shared platform kernel imported by every phase service. No phase business logic
lives here.

| Module | Purpose |
| --- | --- |
| `config.py` | Layered settings (environment → `.env` → defaults) and `Secret` values (W0.4) |
| `dotenv.py` | stdlib-only `.env` subset reader (W0.4) |
| `secrets.py` | `Secret`: redacted in every rendering, unwrapped only on explicit request (W0.4) |
| `db.py` | Schema registry and transaction-local `search_path` sessions (W0.5) |
| `storage.py` | Immutable, content-addressed objects on MinIO (W0.6) |
| `temporal.py` | Temporal connectors, task-queue registry and workflow conventions (W0.7) |
| `heartbeat.py` | Platform heartbeat workflow; lets a fresh checkout register a worker (W0.7) |
| `worker.py` | Runnable worker: `python -m plm_platform.worker` (W0.7) |
| `correlation.py` | Correlation-id context variable, header middleware, dependency (W0.8) |
| `observability.py` | JSON logging, Prometheus metrics, per-run cost counters (W0.8) |
| `cli.py` | The `plm` operator CLI: doctor gate, migration runner, one group per phase (W0.12) |

## Temporal conventions (W0.7)

Temporal is the durable workflow engine (architecture PART XIX).

**Task queues are defined once.** `temporal.TaskQueue` holds exactly one queue
per phase — `ph1.market`, `ph2.ar`, `ph3.experiments`, `ph4.music`, `ph5.qa`,
`ph6.media`, `ph7.youtube`, `ph8.distribution`, `ph9.analytics` — and
`task_queue(phase)` is the typed accessor. Call sites never spell a queue name as
a string literal; the worker CLI accepts either a queue name or a phase token.

**Workflow IDs are business keys, not random UUIDs.** Build them with
`business_workflow_id("music", "track", track_id)`. Two dispatches of the same
business intent produce the same id, so Temporal's duplicate-start arbitration
applies and operators can find an execution from its business identity.
`workflow.info().workflow_id` is that key inside workflow code.

**Every workflow that performs an external side effect is idempotent by
contract.** A side-effecting activity therefore takes an explicit
`idempotency_key: str` argument, built with
`idempotency_key(workflow_id, step)` — the business workflow id plus a stable
step name, never a clock, attempt number or random value:

```python
await workflow.execute_activity(
    upload_master,
    args=[manifest_uri, idempotency_key(workflow.info().workflow_id, "upload")],
    start_to_close_timeout=timedelta(minutes=10),
    retry_policy=DEFAULT_RETRY_POLICY,
)
```

The activity forwards the key to the provider when the provider supports one,
and uses it for local dedupe where it does not. A retried activity must observe
the effect of the earlier attempt and return it, not repeat it.

**Default retry policy** (`DEFAULT_RETRY_POLICY`):

| Knob | Value |
| --- | --- |
| Initial interval | 1 s |
| Backoff coefficient | 2.0 |
| Maximum interval | 60 s |
| Maximum attempts | 5 |
| Non-retryable classes | `ValidationFailure`, `PolicyRejection`, `CreativeRejection` |

The non-retryable classes derive from `NonRetryableError` and fail fast: a
malformed request, a rights/policy refusal or a creative rejection cannot be
fixed by another attempt. Transport failures (timeouts, provider 5xx, rate
limits) are what retries are for.

**Global constraint — "No silent transformation" (P0 §2): a creative failure
must never be retried as a transport failure (or the reverse).** A rejected
creation is a business outcome that policy re-decides; a network blip is
retried under the policy above. Neither may be disguised as the other, and
neither may be silently absorbed.

## Observability conventions (W0.8)

- **JSON logging.** `configure_logging()` installs the platform handler: one
  JSON object per line with timestamp, level, logger, message, `correlation_id`
  and `run_id` when bound, plus structured extras. No bare `print` anywhere in
  `plm_platform` — enforced by a test.
- **Correlation ids.** Inbound HTTP carries `X-Correlation-ID`
  (`CorrelationIdMiddleware` for any ASGI app, or `correlation_id_dependency`
  for FastAPI `Depends`); a missing header is minted. Temporal convention: the
  caller passes the id into the workflow as an argument, and every
  side-effecting activity takes a `correlation_id` parameter and wraps its body
  in `correlation_scope(...)` — see `tests/integration/temporal_sample.py`.
- **Redaction by construction.** A `Secret` renders as `**********` in `%s`
  arguments, dicts and structured extras alike; the JSON encoder falls back to
  `str()`, so there is no path that leaks the value.
- **Metrics.** `plm_operations_total` (counter by operation/outcome) and
  `plm_operation_seconds` (histogram) live on the dedicated `METRICS_REGISTRY`;
  scrape with `MetricsApp` at `/metrics` or any handler returning
  `metrics_payload()`.
- **Run costs.** `CostTracker`/`run_scope` keep the five per-run counters
  (`api_calls`, `llm_tokens`, `llm_cost_estimate`, `storage_bytes`,
  `render_seconds`) keyed by run id; a phase enforces its budget by reading
  `tracker.totals(run_id)` — totals are snapshots, so reading cannot mutate
  them.

## Running a worker

```
python -m plm_platform.worker                                  # all nine queues
python -m plm_platform.worker --task-queue ph4.music \
    --load services.music_factory.worker:REGISTRATIONS
python -m plm_platform.worker --healthcheck --task-queue ph1.market
```

`--load module:attribute` resolves a `WorkerSet` (or a sequence of them); sets
for queues this process does not serve are skipped with a warning. Every worker
also registers `PlatformHeartbeat`, so a fresh checkout can start a worker
before any phase exists. `--healthcheck` exits 0 only when Temporal is reachable
and every selected queue has a live poller; the compose worker uses it. The dev
compose worker serves `ph1.market` only, so phase task queues stay free for
tests and ad-hoc workers.

## Operator CLI (`plm`, W0.12)

One CLI for the whole program (ruling R20), installed as the `plm` console
script; `python -m plm_platform.cli` is equivalent and is what `make.ps1` and
the `Makefile` call. `plm doctor` is the Wave 0 gate (PostgreSQL reachability
and alembic revision vs head, MinIO, Temporal, the nine task-queue constants;
PASS/FAIL table, non-zero exit on any FAIL). `plm migrate [REVISION]` runs the
alembic chain (default `head`). The nine phase groups (`market`, `ar`,
`experiments`, `music`, `qa`, `media`, `youtube`, `distribution`, `analytics`)
are registered stubs until their phase is built: invoking one prints
`not implemented until PHASE-Pn` and exits 2 — the surface is discoverable in
Wave 0 and never silently succeeds.

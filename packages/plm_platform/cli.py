"""The ``plm`` operator CLI (W0.12) — one entry point for the whole program.

One CLI, one command group per phase (ruling R20), because several phase
specifications define no operator surface of their own (P1, P2, P3, P6, P9)
while P5 defines a detailed one. Wave 0 registers every group with its first
command as a *stub*: invoking an unimplemented command prints
``not implemented until PHASE-Pn`` and exits non-zero (:data:`NOT_IMPLEMENTED_EXIT_CODE`)
— never a silent success.

Two commands are real, because the Wave 0 gate depends on them:

``plm doctor``
    Checks PostgreSQL reachability and whether the database is at the alembic
    head, MinIO reachability, Temporal reachability, that the nine phase
    task-queue constants (and the platform queues) are intact, each phase API's
    ``GET /readyz`` (R3: one container per phase, base URLs from
    :mod:`plm_platform.service_urls`), and that the deployed workers' queues have
    live pollers (``ph1.market`` and ``pipeline``). Prints a PASS/FAIL table and exits
    non-zero when anything fails. An unreachable dependency is reported as a
    FAIL row — the command never raises a traceback for one, and it never
    prints a secret (messages are redacted against the configured secrets).

``plm migrate``
    Wraps the single alembic chain (``upgrade`` to a target, default ``head``).

Run it as the installed console script (``plm ...``; registered in
``pyproject.toml``) or equivalently as ``python -m plm_platform.cli ...``,
which is what ``make.ps1`` uses so it does not depend on PATH or activation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, NoReturn

import httpx
import typer
import urllib3
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from minio import Minio
from plm_contracts.money import Money
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from temporalio.api.workflowservice.v1 import GetSystemInfoRequest

from .clock import SystemClock, require_aware
from .config import Settings, SettingsError, load_settings
from .db import database_url, redacted_database_url
from .ledger import BudgetLedger
from .migration_safety import is_disposable_migration_target, user_object_count
from .service_urls import LOOPBACK_HOST, service_urls
from .storage import bucket_name, split_endpoint
from .temporal import (
    Phase,
    TaskQueue,
    all_task_queues,
    connect_client,
    platform_task_queues,
    queue_is_polling,
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
"""The checkout this CLI runs from (the platform is installed editable from it)."""

NOT_IMPLEMENTED_EXIT_CODE: Final[int] = 2
"""Exit code of a registered-but-unbuilt phase command (stub marker, not a usage error)."""

BLOCKED_EXIT_CODE: Final[int] = 3
"""Exit code for "blocked with a reason code" (e.g. pending credentials) — never success."""

_DOCTOR_TITLE: Final[str] = "plm doctor - Wave 0 gate"
_CONNECT_TIMEOUT_SECONDS: Final[int] = 3
_TEMPORAL_TIMEOUT_SECONDS: Final[float] = 5.0
_MINIO_PROBE_TIMEOUT_SECONDS: Final[float] = 2.0
_READYZ_TIMEOUT_SECONDS: Final[float] = 3.0
DEPLOYED_WORKER_QUEUES: Final[tuple[TaskQueue, ...]] = (TaskQueue.MARKET, TaskQueue.PIPELINE)
"""Queues the compose stack serves (``worker`` and ``pipeline-worker``); doctor requires a
live poller on each."""
_FAILURE_MESSAGE_LIMIT: Final[int] = 160

PHASES_BY_GROUP: Final[dict[str, str]] = {
    "market": "PHASE-P1",
    "ar": "PHASE-P2",
    "experiments": "PHASE-P3",
    "music": "PHASE-P4",
    "qa": "PHASE-P5",
    "media": "PHASE-P6",
    "youtube": "PHASE-P7",
    "distribution": "PHASE-P8",
    "analytics": "PHASE-P9",
}
"""Group → owning phase, for the stub messages and for tests."""

app = typer.Typer(
    name="plm",
    help=(
        "PLM platform operator CLI. `doctor` is the Wave 0 gate; `migrate` runs the "
        "alembic chain; the phase groups are registered stubs until their phase is "
        f"built (invoking one exits {NOT_IMPLEMENTED_EXIT_CODE})."
    ),
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------------------
# doctor: the Wave 0 gate


@dataclass(frozen=True, slots=True)
class Check:
    """One doctor row."""

    name: str
    passed: bool
    detail: str


def run_doctor(
    *,
    connect_timeout: float = _CONNECT_TIMEOUT_SECONDS,
    temporal_timeout: float = _TEMPORAL_TIMEOUT_SECONDS,
) -> list[Check]:
    """Run every Wave 0 health check; never raises for an unreachable dependency.

    Kept separate from the Typer command so tests (and a future ``doctor``
    endpoint) can call it directly; the command only renders and sets the
    exit code.
    """
    checks: list[Check] = []
    settings: Settings | None = None
    try:
        settings = load_settings(dotenv_path=REPO_ROOT / ".env")
    except SettingsError as exc:
        checks.append(Check("config", False, f"settings could not load: {exc}"))
    else:
        checks.append(
            Check(
                "config",
                True,
                f"env={settings.env} log_level={settings.log_level} "
                f"database={redacted_database_url(settings)}",
            )
        )
    if settings is None:
        for name in ("postgres", "migrations", "minio", "temporal", "worker_pollers"):
            checks.append(Check(name, False, "not checked - configuration failed to load"))
    else:
        checks.append(_postgres_check(settings, connect_timeout))
        checks.append(_migrations_check(settings, connect_timeout))
        checks.append(_minio_check(settings))
        checks.append(_temporal_check(settings, temporal_timeout))
        checks.append(_worker_pollers_check(settings, temporal_timeout))
    checks.append(_task_queues_check())
    checks.extend(_readyz_checks())
    return checks


def _postgres_check(settings: Settings, connect_timeout: float) -> Check:
    target = _database_target(settings)
    engine = _engine(settings, connect_timeout)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SHOW server_version")).scalar_one()
    except Exception as exc:  # noqa: BLE001 - doctor reports failures, it never raises them
        return Check("postgres", False, f"unreachable at {target} - {_describe(exc, settings)}")
    finally:
        engine.dispose()
    return Check("postgres", True, f"reachable at {target} (PostgreSQL {version})")


# Heads installed in CODE ONLY: `plm migrate` refuses to apply them without a
# separately approved rollout. Every new code-only revision must be added here,
# otherwise becoming the head would silently lift the refusal.
_CODE_ONLY_HEADS = frozenset({
    "0039_merge_0038_branches", "0040_p4_unpriced_costs",
    "0041_p4_credit_provenance", "0042_media_render_evidence",
})


def _migration_revision_result(
    current: str | None,
    head: str | None,
    known_revisions: frozenset[str],
) -> Check:
    """Distinguish an ordinary pending upgrade from an unknown revision branch."""
    if head is None:
        return Check(
            "migrations", False,
            "checkout has no Alembic head - do NOT run plm migrate before reviewing revisions",
        )
    if current == head:
        return Check("migrations", True, f"at {current} (head)")
    if current is None:
        if head in _CODE_ONLY_HEADS:
            return Check(
                "migrations", False,
                f"database not migrated; {head} installed in CODE ONLY - "
                "do NOT run plm migrate without separate database rollout approval",
            )
        return Check(
            "migrations", False,
            f"database not migrated (head is {head}); confirm the target is empty "
            "before running plm migrate",
        )
    if current not in known_revisions:
        return Check(
            "migrations", False,
            f"database at {current}, which is NOT in this checkout's migration chain "
            f"(head {head}); possible divergent worktree/branch - do NOT run plm migrate "
            "until revision history is reconciled and reviewed",
        )
    if head in _CODE_ONLY_HEADS:
        return Check(
            "migrations", False,
            f"database at {current}, head {head}; pending revisions installed in CODE ONLY - "
            "do NOT run plm migrate without separate database rollout approval",
        )
    return Check("migrations", False, f"database at {current}, head is {head} - run: plm migrate")


def _migrations_check(settings: Settings, connect_timeout: float) -> Check:
    try:
        script = ScriptDirectory.from_config(_alembic_config())
        head = script.get_current_head()
        known_revisions = frozenset(r.revision for r in script.walk_revisions())
    except Exception as exc:  # noqa: BLE001 - a broken checkout is a FAIL row too
        return Check("migrations", False, f"alembic configuration unreadable - {_describe(exc)}")
    engine = _engine(settings, connect_timeout)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    except Exception as exc:  # noqa: BLE001
        return Check(
            "migrations",
            False,
            f"cannot read the current revision - {_describe(exc, settings)}",
        )
    finally:
        engine.dispose()
    return _migration_revision_result(current, head, known_revisions)


def _minio_check(settings: Settings) -> Check:
    try:
        exists = _minio_probe_client(settings).bucket_exists(bucket_name(settings))
    except Exception as exc:  # noqa: BLE001
        return Check(
            "minio", False, f"unreachable at {settings.minio_endpoint} - {_describe(exc, settings)}"
        )
    state = "present" if exists else "missing (create it via ensure_bucket, e.g. any storage test)"
    return Check(
        "minio",
        True,
        f"reachable at {settings.minio_endpoint}; bucket {bucket_name(settings)!r} {state}",
    )


def _minio_probe_client(settings: Settings) -> Minio:
    """A MinIO client for one probe attempt — no retries, bounded timeouts.

    The production client (:func:`plm_platform.storage.build_client`) retries
    five times with exponential backoff, which is right for artifact
    operations and wrong for a health probe: an unreachable endpoint would
    hold the doctor table for ~20 seconds. Bucket existence is still checked
    with the configured credentials, so the probe also exercises auth.
    """
    endpoint, secure = split_endpoint(settings.minio_endpoint)
    timeout = urllib3.Timeout(
        connect=_MINIO_PROBE_TIMEOUT_SECONDS, read=_MINIO_PROBE_TIMEOUT_SECONDS
    )
    return Minio(
        endpoint,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password.get_secret_value(),
        secure=secure,
        http_client=urllib3.PoolManager(retries=urllib3.Retry(total=0), timeout=timeout),
    )


def _temporal_check(settings: Settings, timeout: float) -> Check:
    try:
        version = asyncio.run(asyncio.wait_for(_temporal_probe(settings), timeout=timeout))
    except TimeoutError:
        return Check(
            "temporal",
            False,
            f"unreachable at {settings.temporal_host} - timed out after {timeout:g}s",
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "temporal",
            False,
            f"unreachable at {settings.temporal_host} - {_describe(exc, settings)}",
        )
    return Check("temporal", True, f"reachable at {settings.temporal_host} (server {version})")


async def _temporal_probe(settings: Settings) -> str:
    client = await connect_client(settings)
    info = await client.workflow_service.get_system_info(GetSystemInfoRequest())
    return str(info.server_version)


def _task_queues_check() -> Check:
    expected = tuple(f"{phase}.{phase.name.lower()}" for phase in Phase)
    actual = tuple(queue.value for queue in all_task_queues())
    platform = tuple(queue.value for queue in platform_task_queues())
    phase_queues = [queue for queue in TaskQueue if queue.value.startswith("ph")]
    if len(phase_queues) == len(Phase) == len(expected) and actual == expected and platform:
        return Check(
            "task_queues", True,
            "9 phase queues intact (ph1.market .. ph9.analytics); platform: "
            + ", ".join(platform),
        )
    drift = [f"{want} != {got}" for want, got in zip(expected, actual) if want != got]
    if len(actual) != len(expected) or len(phase_queues) != len(expected):
        drift.append(f"{len(phase_queues)} phase queues, expected {len(expected)}")
    if not platform:
        drift.append("no platform queue")
    return Check("task_queues", False, "constants drifted: " + "; ".join(drift or ["unknown"]))


def _worker_pollers_check(settings: Settings, timeout: float) -> Check:
    """The deployed workers are alive *and polling* (process liveness is not health)."""
    async def probe() -> list[str]:
        client = await connect_client(settings)
        return [queue.value for queue in DEPLOYED_WORKER_QUEUES
                if not await queue_is_polling(client, queue)]

    try:
        silent = asyncio.run(asyncio.wait_for(probe(), timeout=timeout))
    except Exception as exc:  # noqa: BLE001 - doctor reports failures, it never raises them
        return Check("worker_pollers", False, f"cannot ask Temporal - {_describe(exc, settings)}")
    queues = ", ".join(queue.value for queue in DEPLOYED_WORKER_QUEUES)
    if silent:
        return Check("worker_pollers", False,
                     f"no poller on {', '.join(silent)} (start: docker compose up -d --wait)")
    return Check("worker_pollers", True, f"pollers present on {queues}")


def _readyz_checks() -> list[Check]:
    """``GET /readyz`` on every phase API, concurrently (R3: one container per phase).

    Base URLs: ``PLM_SERVICE_URL__<PHASE>`` or the loopback port compose publishes.
    """
    try:
        urls = service_urls(dotenv_path=REPO_ROOT / ".env", default_host=LOOPBACK_HOST)
    except SettingsError as exc:
        return [Check("api_readyz", False, str(exc))]
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        return list(pool.map(lambda item: _readyz_check(*item), urls.items()))


def _readyz_check(phase: Phase, base_url: str) -> Check:
    name = f"api_{phase.name.lower()}"
    target = f"{base_url}/readyz"
    try:
        response = httpx.get(target, timeout=_READYZ_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        return Check(name, False, f"unreachable at {target} - {type(exc).__name__}")
    try:
        body = response.json()
    except ValueError:
        body = {}
    reason = body.get("reason") if isinstance(body, dict) else None
    if response.status_code == 200 and isinstance(body, dict) and body.get("status") == "ready":
        return Check(name, True, f"ready at {target}")
    return Check(name, False,
                 f"not ready at {target} - HTTP {response.status_code}"
                 + (f" ({reason})" if reason else ""))


def _render_table(checks: Sequence[Check]) -> str:
    width = max(len(check.name) for check in checks)
    lines = [f"{'check':<{width}}  {'status':<6}  detail", f"{'-' * width}  {'-' * 6}  {'-' * 6}"]
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"{check.name:<{width}}  {status:<6}  {check.detail}")
    passed = sum(1 for check in checks if check.passed)
    lines.extend(["", f"{len(checks)} checks: {passed} PASS, {len(checks) - passed} FAIL"])
    return "\n".join(lines)


def _engine(settings: Settings, connect_timeout: float) -> Engine:
    return create_engine(
        database_url(settings),
        poolclass=NullPool,
        connect_args={"connect_timeout": int(connect_timeout)},
    )


def _database_target(settings: Settings) -> str:
    url = database_url(settings)
    return f"{url.host}:{url.port}/{url.database}"


def _alembic_config() -> Config:
    """Alembic configuration pinned to the checkout (alembic.ini uses ``%(here)s``)."""
    return Config(str(REPO_ROOT / "alembic.ini"))


def _describe(exc: BaseException, settings: Settings | None = None) -> str:
    """A one-line failure description with configured secrets redacted."""
    message = _redact(" ".join(str(exc).split()), settings)
    described = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    if len(described) <= _FAILURE_MESSAGE_LIMIT:
        return described
    return described[: _FAILURE_MESSAGE_LIMIT - 3] + "..."


def _redact(message: str, settings: Settings | None) -> str:
    if settings is None:
        return message
    secrets: Iterable[str] = (
        settings.database_url.get_secret_value(),
        settings.postgres_password.get_secret_value(),
        settings.minio_root_password.get_secret_value(),
    )
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return message


@app.command()
def doctor() -> None:
    """PostgreSQL + migration head, MinIO, Temporal, worker pollers, task queues, phase APIs."""
    typer.echo(_DOCTOR_TITLE)
    checks = run_doctor()
    typer.echo(_render_table(checks))
    failures = [check for check in checks if not check.passed]
    if failures:
        typer.echo(f"doctor: {len(failures)} check(s) failed", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# migrate: the single alembic chain


def _unversioned_target_refusal(existing_objects: object) -> str | None:
    """Never treat an unversioned but populated database as a fresh baseline."""
    if type(existing_objects) is not int or existing_objects < 0:
        return "cannot verify that the unversioned migration target is empty"
    if existing_objects:
        return (
            "unversioned database contains existing user schemas or public objects; "
            "refusing migration. Inspect and reconcile its history before upgrade"
        )
    return None


def _merge_code_only_refusal(
    head: str, current: str | None, *, disposable: bool = False,
) -> str | None:
    """Hold the 0039 rollout until separately approved, even after code installation.

    A disposable target (`is_disposable_migration_target`, the rule raw Alembic
    already applies) is exempt; every other database stays held.
    """
    if head in _CODE_ONLY_HEADS and current != head and not disposable:
        return (
            "0039-0042 revisions are installed in code only; database migration "
            "is NOT approved. Refusing plm migrate until a separately "
            "reviewed rollout/restore plan."
        )
    return None


def _migration_preflight(config: Config) -> None:
    """Read-only guard against foreign revisions and unapproved 0039 rollout.

    The revision table must be reachable and belong to this checkout's sole
    chain. Never stamp, downgrade or guess at an unknown branch.
    """
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    known = frozenset(item.revision for item in script.walk_revisions())
    if head is None:
        raise RuntimeError("checkout has no migration head; reconcile revisions before upgrade")
    settings = load_settings(dotenv_path=REPO_ROOT / ".env")
    engine = _engine(settings, _CONNECT_TIMEOUT_SECONDS)
    disposable = False
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
            if current is None:
                # A missing version does not imply an empty database.
                # Both the operator CLI and destructive test use this same
                # read-only catalog inventory.
                reason = _unversioned_target_refusal(user_object_count(connection))
                if reason is not None:
                    raise RuntimeError(reason)
            if head in _CODE_ONLY_HEADS and current != head:
                url = database_url(settings)
                disposable = is_disposable_migration_target(
                    configured_database=url.database,
                    connected_database=connection.execute(
                        text("SELECT current_database()")
                    ).scalar_one(),
                    host=url.host,
                    port=url.port,
                )
    finally:
        engine.dispose()
    if current is not None and current not in known:
        raise RuntimeError(
            f"database revision {current} is not in this checkout's Alembic chain "
            f"(head {head}); refusing migration. Reconcile branch history first."
        )
    merge_hold = _merge_code_only_refusal(head, current, disposable=disposable)
    if merge_hold is not None:
        raise RuntimeError(merge_hold)


@app.command()
def migrate(
    revision: str = typer.Argument("head", help="Target revision (default: head)."),
) -> None:
    """Upgrade the database along the alembic chain to REVISION (default: head)."""
    config = _alembic_config()
    try:
        _migration_preflight(config)
        command.upgrade(config, revision)
    except Exception as exc:
        typer.echo(
            f"migrate: alembic upgrade to {revision!r} failed - {_describe_for_cli(exc)}", err=True
        )
        raise typer.Exit(code=1) from exc
    typer.echo(f"migrate: database upgraded to {revision!r}")


def _describe_for_cli(exc: BaseException) -> str:
    """Best-effort redaction outside the doctor path (settings may be unloadable)."""
    try:
        settings: Settings | None = load_settings(dotenv_path=REPO_ROOT / ".env")
    except SettingsError:
        settings = None
    return _describe(exc, settings)


# ---------------------------------------------------------------------------
# phase command groups (registered stubs until their phase is built)


def _not_implemented(phase: str) -> NoReturn:
    typer.echo(
        f"not implemented until {phase}: the command group is registered in Wave 0 so "
        "the operator surface is discoverable; the phase's implementation replaces this stub",
        err=True,
    )
    raise typer.Exit(code=NOT_IMPLEMENTED_EXIT_CODE)


market_app = typer.Typer(help="P1 market intelligence: evidence collection and scoring.")
ar_app = typer.Typer(help="P2 autonomous AR: allocation cycles and posteriors.")
experiments_app = typer.Typer(help="P3 experiment framework: assignment and evaluation.")
music_app = typer.Typer(help="P4 music generation: candidate production.")
qa_app = typer.Typer(help="P5 quality & rights gate: review queue and dispositions.")
media_app = typer.Typer(help="P6 media factory: playlist and visual renders.")
youtube_app = typer.Typer(help="P7 YouTube publishing: uploads and channel state.")
distribution_app = typer.Typer(help="P8 DSP distribution: submissions and reports.")
analytics_app = typer.Typer(help="P9 analytics & economics: ingestion and periods.")


@market_app.command()
def collect(refresh: bool = False, limit: int = 25) -> None:
    """Collect market evidence for due queries (P1)."""
    from market_intelligence.cli import run_collect

    run_collect(refresh=refresh, limit=limit)


@market_app.command()
def score() -> None:
    """Recompute market opportunity scores (P1)."""
    from market_intelligence.cli import run_score

    run_score()


@market_app.command()
def report(as_of: str = "") -> None:
    """Write the daily market snapshot and report (P1)."""
    from market_intelligence.cli import run_report

    typer.echo(run_report(as_of=as_of or None))


@market_app.command("run-daily")
def run_daily_command() -> None:
    """Run one daily market cycle in-process (P1)."""
    from market_intelligence.cli import run_daily

    typer.echo(json.dumps(run_daily(), indent=2, default=str))


@market_app.command()
def serve(host: str = "127.0.0.1", port: int = 8081) -> None:
    """Serve the `/market/v1` API (P1)."""
    from market_intelligence.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@market_app.command("smoke-live")
def smoke_live() -> None:
    """Run the §40 live smoke test; blocked (exit 3) until credentials exist."""
    from market_intelligence.cli import smoke_live as smoke_impl

    reason = smoke_impl()
    if reason:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)
    typer.echo("live smoke: PASS")



@ar_app.command()
def cycle(
    slot_count: int = 10, budget: float = 10.0, policy_version: str = "p2-v1", as_of: str = ""
) -> None:
    """Run one allocation cycle (P2)."""
    from autonomous_ar.cli import run_cycle_once

    typer.echo(
        json.dumps(
            run_cycle_once(
                slot_count=slot_count,
                budget=budget,
                policy_version=policy_version,
                as_of=as_of or None,
            ),
            indent=2,
            default=str,
        )
    )


@ar_app.command()
def show(cycle_id: str) -> None:
    """Show one cycle's status and counts (P2)."""
    from autonomous_ar.cli import show_cycle

    typer.echo(show_cycle(cycle_id))


@ar_app.command()
def arms() -> None:
    """List arm states and posterior summaries (P2)."""
    from autonomous_ar.cli import list_arms

    typer.echo(list_arms())


@ar_app.command()
def explain(decision_id: str) -> None:
    """Explain one decision from its stored rows (P2)."""
    from autonomous_ar.cli import explain as explain_impl

    typer.echo(explain_impl(decision_id))


@ar_app.command(name="serve")
def ar_serve(host: str = "127.0.0.1", port: int = 8082) -> None:
    """Serve the `/ar` API (P2). (`ar_serve` because the market group owns `serve`.)"""
    from autonomous_ar.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@ar_app.command()
def simulate(scenario: str = "clear_winner", cycles: int = 80, seed: int = 1) -> None:
    """Run one synthetic scenario and print its metrics (P2)."""
    from autonomous_ar.cli import simulate as simulate_impl

    typer.echo(simulate_impl(scenario, cycles, seed))


@ar_app.command()
def smoke() -> None:
    """Run the §114 P2.16 dry-run smoke; blocked (exit 3) until P1 is reachable with a token."""
    from autonomous_ar.acceptance.production_smoke import run_production_smoke

    reason = run_production_smoke()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)


@experiments_app.command(name="list")
def experiments_list(limit: int = 20) -> None:
    """List recent experiments (P3)."""
    from experiments.cli import list_experiments

    typer.echo(list_experiments(limit=limit))


@experiments_app.command(name="show")
def experiments_show(experiment_id: str) -> None:
    """Show one experiment's definition (P3)."""
    from experiments.cli import show_experiment

    typer.echo(show_experiment(experiment_id))


@experiments_app.command(name="validate")
def experiments_validate(experiment_id: str) -> None:
    """Run preflight validation on an experiment (P3)."""
    from experiments.cli import validate_experiment_cli

    typer.echo(validate_experiment_cli(experiment_id))


@experiments_app.command(name="materialize")
def experiments_materialize(
    experiment_id: str,
    treatment_id: str,
    brand_id: str = "neon_seoul",
    budget_envelope: float | None = None,
) -> None:
    """Materialize an experiment treatment into a ContentPackage (P3).

    Without ``--budget-envelope`` the directive's budget limit is used; a larger one is
    refused. The track count is planned from the genome's declared playlist range.
    """
    from experiments.cli import materialize_treatment_cli

    typer.echo(
        materialize_treatment_cli(
            experiment_id,
            treatment_id,
            brand_id=brand_id,
            budget_envelope=budget_envelope,
        )
    )


@experiments_app.command(name="serve")
def experiments_serve(host: str = "127.0.0.1", port: int = 8083) -> None:
    """Serve the `/experiments` API (P3)."""
    from experiments.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@experiments_app.command(name="smoke")
def experiments_smoke() -> None:
    """Run the P3 dry-run smoke test."""
    from experiments.acceptance.smoke import run_smoke_test

    reason = run_smoke_test()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)


@music_app.command(name="generate")
def music_generate(
    spec_path: str = typer.Option(
        ..., "--spec", "-s", help="Path to JSON file containing TrackSpec"
    ),
    provider: str = typer.Option(
        "mock", "--provider", "-p", help="Provider to use (mock, suno, elevenlabs)"
    ),
) -> None:
    """Generate P4 music candidates from a TrackSpec JSON file."""
    from music_factory.cli import cli_generate

    cli_generate(spec_path=spec_path, provider=provider)


@music_app.command(name="providers")
def music_providers() -> None:
    """List registered AI music generation providers."""
    from music_factory.cli import cli_providers

    cli_providers()


@music_app.command(name="serve")
def music_serve(host: str = "127.0.0.1", port: int = 8004) -> None:
    """Serve the `/music` API (P4)."""
    from music_factory.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@music_app.command(name="smoke")
def music_smoke() -> None:
    """Run the P4 dry-run smoke test."""
    from music_factory.acceptance.smoke import run_smoke_test

    reason = run_smoke_test()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)


@qa_app.command(name="review")
def qa_review() -> None:
    """Work the P5 review queue (quarantined candidates)."""
    from quality_gate.cli import cli_review

    cli_review()


@qa_app.command(name="evaluate")
def qa_evaluate(
    event_path: str = typer.Option(
        ..., "--event", "-e", help="Path to a Seam C4 candidate-ready event (JSON)"
    ),
    audio_path: str = typer.Option(..., "--audio", "-f", help="Path to WAV audio file"),
) -> None:
    """Evaluate a candidate, described by its C4 event, against the release gates."""
    from quality_gate.cli import cli_evaluate

    cli_evaluate(event_path=event_path, audio_path=audio_path)


@qa_app.command(name="manifest")
def qa_manifest(
    candidate_id: str = typer.Option(
        ..., "--candidate-id", "-c", help="Candidate ID to lookup manifest for"
    ),
) -> None:
    """Retrieve the Seam C5 release manifest for a cleared candidate."""
    from quality_gate.cli import cli_manifest

    cli_manifest(candidate_id=candidate_id)


@qa_app.command(name="resolve")
def qa_resolve(
    case_id: str = typer.Option(..., "--case-id", help="Quarantine case ID"),
    resolution: str = typer.Option(
        "CLEARED_BY_OVERRIDE",
        "--resolution",
        help="Resolution: CLEARED_BY_OVERRIDE or CONFIRMED_REJECTED",
    ),
    auditor_id: str = typer.Option("auditor_cli", "--auditor", help="Auditor identity"),
    notes: str = typer.Option("", "--notes", help="Audit notes"),
) -> None:
    """Resolve an open quarantine case."""
    from quality_gate.cli import cli_resolve

    cli_resolve(
        case_id=case_id,
        resolution=resolution,
        auditor_id=auditor_id,
        notes=notes,
    )


@qa_app.command(name="serve")
def qa_serve(host: str = "127.0.0.1", port: int = 8005) -> None:
    """Serve the `/qa` API (P5)."""
    from quality_gate.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@qa_app.command(name="smoke")
def qa_smoke() -> None:
    """Run the P5 dry-run smoke test."""
    from quality_gate.acceptance.smoke import run_smoke_test

    reason = run_smoke_test()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)


@media_app.command(name="assemble")
def media_assemble(
    request_path: str = typer.Option(
        ...,
        "--request",
        "-r",
        help="Path to an assembly request JSON (candidate_ids, brand_id, duration, visual "
        "profile, playlist concept)",
    ),
) -> None:
    """Assemble and render a media package from recordings P5 cleared (by candidate id)."""
    from media_factory.cli import cli_assemble

    cli_assemble(request_path=request_path)


@media_app.command(name="render-jobs")
def media_render_jobs(
    package_id: str = typer.Option(
        ..., "--package-id", "-p", help="Exact media package to inspect",
    ),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
) -> None:
    """Read persisted P6 render history without changing or retrying work."""
    from media_factory.cli import cli_render_jobs

    cli_render_jobs(package_id=package_id, limit=limit)


@media_app.command(name="render-inspect")
def media_render_inspect(
    job_id: str = typer.Option(..., "--job-id", help="Exact persisted render job ID"),
    brand_id: str = typer.Option(..., "--brand-id", help="Explicit brand ID path hint"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    record_inventory: bool = typer.Option(
        False, "--record-inventory",
        help="Persist a complete listing of a QUARANTINED claim as recovery evidence",
    ),
    recorded_by: str | None = typer.Option(None, "--recorded-by", help="Operator identity"),
) -> None:
    """B02 object-name inventory, optionally recorded as evidence; not recovery or reclaim."""
    from media_factory.cli import cli_render_inspect

    cli_render_inspect(job_id=job_id, brand_id=brand_id, limit=limit,
                       record_inventory=record_inventory, recorded_by=recorded_by)


@media_app.command(name="render-attest-termination")
def media_render_attest_termination(
    job_id: str = typer.Option(..., "--job-id", help="Exact RUNNING render job ID"),
    evidence_ref: str = typer.Option(..., "--evidence-ref", help="Out-of-band proof reference"),
    recorded_by: str = typer.Option(..., "--recorded-by", help="Operator identity"),
) -> None:
    """Record verified worker termination and fence the claim (ADR-0043); no retry."""
    from media_factory.cli import cli_render_attest_termination

    cli_render_attest_termination(job_id=job_id, evidence_ref=evidence_ref,
                                  recorded_by=recorded_by)


@media_app.command(name="package")
def media_package(
    package_id: str = typer.Option(..., "--package-id", "-p", help="Media package ID to inspect"),
) -> None:
    """Retrieve an assembled media package record."""
    from media_factory.cli import cli_package

    cli_package(package_id=package_id)


@media_app.command(name="inspect-album-textures")
def media_inspect_album_textures(
    brand_id: str = typer.Option(..., "--brand-id", help="Exact brand ID"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    after_id: str | None = typer.Option(None, "--after-id"),
) -> None:
    """Read-only paginated metadata inventory for archived album texture."""
    from media_factory.cli import cli_inspect_album_textures

    cli_inspect_album_textures(brand_id=brand_id, limit=limit, after_id=after_id)


@media_app.command(name="backfill-album-texture")
def media_backfill_album_texture(
    package_id: str = typer.Option(..., "--package-id", "-p", help="Exact archived package ID"),
    apply: bool = typer.Option(False, "--apply", help="Write verified evidence (default: dry run)"),
) -> None:
    """Backfill original archived audio texture, without generating or publishing music."""
    from media_factory.cli import cli_backfill_album_texture

    cli_backfill_album_texture(package_id=package_id, apply=apply)


@media_app.command(name="backfill-album-textures")
def media_backfill_album_textures(
    package_ids: list[str] = typer.Option(  # noqa: B008 - Typer CLI declaration
        ..., "--package-id", "-p", help="Repeat for each archived package (max 20)",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Commit verified packages individually (default: dry run)",
    ),
) -> None:
    """Backfill multiple explicitly selected archived albums without external writes."""
    from media_factory.cli import cli_backfill_album_textures

    cli_backfill_album_textures(package_ids=package_ids, apply=apply)


@media_app.command(name="serve")
def media_serve(host: str = "127.0.0.1", port: int = 8006) -> None:
    """Serve the `/media` API (P6)."""
    from media_factory.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@media_app.command(name="smoke")
def media_smoke() -> None:
    """Run the P6 dry-run smoke test."""
    from media_factory.acceptance.smoke import run_smoke_test

    reason = run_smoke_test()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)


@youtube_app.command(name="publish")
def youtube_publish(
    package_path: str = typer.Option(
        ..., "--package", "-p", help="Path to media package JSON file"
    ),
    channel_id: str = typer.Option("UC_neon_records", "--channel-id", "-c", help="Target YouTube channel ID"),
    mode: str = typer.Option("dry_run", "--mode", "-m", help="Operating mode: dry_run, private_only, staged, autonomous"),
    metadata_variant_id: str | None = typer.Option(
        None, "--metadata-variant-id", help="Select a declared C6 metadata candidate ID",
    ),
    thumbnail_id: str | None = typer.Option(
        None, "--thumbnail-id", help="Select a declared C6 thumbnail candidate ID",
    ),
) -> None:
    """Publish a media package to YouTube (P7)."""
    from youtube_publishing.cli import cli_publish

    cli_publish(
        package_path=package_path, channel_id=channel_id, mode=mode,
        metadata_variant_id=metadata_variant_id, thumbnail_id=thumbnail_id,
    )


@youtube_app.command(name="attempt")
def youtube_attempt(
    attempt_id: str = typer.Option(..., "--attempt-id", "-a", help="Publish attempt ID"),
) -> None:
    """Retrieve the status of a publish attempt."""
    from youtube_publishing.cli import cli_attempt

    cli_attempt(attempt_id=attempt_id)


@youtube_app.command(name="reconcile")
def youtube_reconcile(
    attempt_id: str = typer.Option(..., "--attempt-id", "-a", help="Publish attempt ID"),
) -> None:
    """Reconcile a publish attempt with remote YouTube state."""
    from youtube_publishing.cli import cli_reconcile

    cli_reconcile(attempt_id=attempt_id)


@youtube_app.command(name="serve")
def youtube_serve(host: str = "127.0.0.1", port: int = 8007) -> None:
    """Serve the `/youtube` API (P7)."""
    from youtube_publishing.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@youtube_app.command(name="smoke")
def youtube_smoke() -> None:
    """Run the P7 dry-run smoke test."""
    from youtube_publishing.acceptance.smoke import run_smoke_test

    reason = run_smoke_test()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)


@distribution_app.command(name="submit")
def distribution_submit(
    package_path: str = typer.Option(
        ...,
        "--request",
        "-r",
        help="Path to a release request JSON: track_manifests (C5), media_package (C6), "
        "optional publication, and the release metadata",
    ),
) -> None:
    """Submit a release to DSPs from seam payloads (P8)."""
    from distribution.cli import cli_submit

    cli_submit(package_path=package_path)


@distribution_app.command(name="release")
def distribution_release(
    release_id: str = typer.Option(..., "--release-id", "-r", help="Distribution release ID"),
) -> None:
    """Show details of a distribution release (P8)."""
    from distribution.cli import cli_release

    cli_release(release_id=release_id)


@distribution_app.command(name="report")
def distribution_report(
    report_id: str = typer.Option(..., "--report-id", "-i", help="Provider report ID"),
) -> None:
    """Show details of an ingested raw provider report (P8, Ruling R10)."""
    from distribution.cli import cli_report

    cli_report(report_id=report_id)


@distribution_app.command(name="serve")
def distribution_serve(host: str = "127.0.0.1", port: int = 8008) -> None:
    """Serve the `/distribution` API (P8)."""
    from distribution.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@distribution_app.command(name="smoke")
def distribution_smoke() -> None:
    """Run the P8 dry-run smoke test."""
    from distribution.acceptance.smoke import run_smoke_test

    reason = run_smoke_test()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)


@analytics_app.command(name="ingest")
def analytics_ingest(
    source: str = typer.Option("distributor", "--source", "-s", help="Source type: youtube, distributor, cost"),
    file_path: str = typer.Option(..., "--file", "-f", help="Path to data file"),
    provider: str | None = typer.Option(None, "--provider", help="Statement provider (distributor)"),
    period: str | None = typer.Option(None, "--period", help="Statement period (distributor)"),
    currency: str | None = typer.Option(None, "--currency", help="Statement currency (distributor)"),
) -> None:
    """Ingest a metrics, statement, or cost file into the analytics ledger (P9).

    A distributor statement needs ``--provider``, ``--period`` and ``--currency``; a
    missing one is refused (exit 2), never filled in.
    """
    from analytics.cli import cli_ingest

    cli_ingest(source=source, file_path=file_path, provider=provider, period=period,
               currency=currency)


@analytics_app.command(name="report")
def analytics_report(
    entity_id: str = typer.Option(..., "--entity-id", "-e", help="Entity ID (video, track, etc.)"),
) -> None:
    """Show unit-economics and performance report for an entity (P9)."""
    from analytics.cli import cli_report

    cli_report(entity_id=entity_id)


@analytics_app.command(name="rewards")
def analytics_rewards(
    experiment_id: str = typer.Option(..., "--experiment-id", "-x", help="Experiment ID"),
) -> None:
    """Show experiment feedback rewards (P9, Ruling R11)."""
    from analytics.cli import cli_rewards

    cli_rewards(experiment_id=experiment_id)


@analytics_app.command(name="dispatch")
def analytics_dispatch(
    limit: int = typer.Option(100, "--limit", help="Most outbox events handled in this pass"),
    ar_base_url: str | None = typer.Option(None, "--ar-base-url", help="P2 base URL override"),
) -> None:
    """Deliver due C7 leg-2 rewards from P9's outbox to P2 (R3; one pass)."""
    from analytics.cli import cli_dispatch

    cli_dispatch(limit=limit, ar_base_url=ar_base_url)


@analytics_app.command(name="serve")
def analytics_serve(host: str = "127.0.0.1", port: int = 8009) -> None:
    """Serve the `/analytics` API (P9)."""
    from analytics.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@analytics_app.command(name="smoke")
def analytics_smoke() -> None:
    """Run the P9 dry-run smoke test."""
    from analytics.acceptance.smoke import run_smoke_test

    reason = run_smoke_test()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)


dashboard_app = typer.Typer(
    help=(
        "Operator dashboard: the six PART XVII views over the live database and phase "
        "APIs, and the Exceptions actions. Fixed demo data only with --demo."
    ),
    no_args_is_help=False,
)

_DEMO_OPTION_HELP = "Serve the fixed demo data and scripted playback instead of live data"
_REVIEWERS_OPTION_HELP = (
    "JSON reviewer directory {actor: [roles]} authorizing exception actions "
    "(default: PLM_DASHBOARD_REVIEWERS_FILE). Without one every action is refused."
)


def _reviewers_file(option: str | None) -> Path | None:
    """The reviewer directory: the option, else the variable (process env, then .env)."""
    import os

    from .dashboard.actions import REVIEWERS_FILE_VAR
    from .dotenv import load_env_file

    if option is not None and option.strip():
        return Path(option)
    raw = os.environ.get(REVIEWERS_FILE_VAR) or load_env_file(Path(".env")).get(
        REVIEWERS_FILE_VAR)
    return Path(raw) if raw and raw.strip() else None


def _serve_dashboard(host: str, port: int, reload: bool, *, demo: bool,
                     reviewers_file: str | None) -> None:
    import uvicorn

    from .config import SERVICE_TOKEN_VAR, SettingsError, load_settings
    from .dashboard.actions import REVIEWERS_FILE_VAR
    from .dashboard.app import create_app

    try:
        platform = load_settings()
    except SettingsError as exc:
        typer.echo(f"settings unavailable ({exc}); live panels report unavailable and "
                   "mutating routes stay closed", err=True)
        platform = None
    if platform is None or platform.service_token is None:
        typer.echo(
            f"{SERVICE_TOKEN_VAR} is not set: mutating routes answer 503 and the phase APIs "
            "refuse the dashboard's reads (R15)", err=True
        )
    if demo:
        typer.echo(f"Starting PLM Control Deck (DEMO DATA) on http://{host}:{port} ...")
        uvicorn.run(create_app(demo=True, platform=platform), host=host, port=port,
                    reload=reload)
        return
    directory = _reviewers_file(reviewers_file)
    if directory is None:
        typer.echo(f"{REVIEWERS_FILE_VAR} is not set: every exception action is refused",
                   err=True)
    try:
        application = create_app(platform=platform, reviewers_file=directory)
    except (OSError, ValueError) as exc:
        typer.echo(f"dashboard: reviewer directory {directory} is unusable: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Starting PLM Control Deck (LIVE DATA) on http://{host}:{port} ...")
    uvicorn.run(application, host=host, port=port, reload=reload)


@dashboard_app.callback(invoke_without_command=True)
def dashboard_main(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host address to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Enable live auto-reloading"),
    demo: bool = typer.Option(False, "--demo", help=_DEMO_OPTION_HELP),
    reviewers_file: str | None = typer.Option(
        None, "--reviewers-file", help=_REVIEWERS_OPTION_HELP),
) -> None:
    """Launch the PLM dashboard (live data; --demo for the fixed sample)."""
    if ctx.invoked_subcommand is None:
        _serve_dashboard(host, port, reload, demo=demo, reviewers_file=reviewers_file)


@dashboard_app.command(name="serve")
def dashboard_serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host address to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Enable live auto-reloading"),
    demo: bool = typer.Option(False, "--demo", help=_DEMO_OPTION_HELP),
    reviewers_file: str | None = typer.Option(
        None, "--reviewers-file", help=_REVIEWERS_OPTION_HELP),
) -> None:
    """Serve the PLM dashboard (live data; --demo for the fixed sample)."""
    _serve_dashboard(host, port, reload, demo=demo, reviewers_file=reviewers_file)


# ---------------------------------------------------------------------------
# budget: the persisted cost ledger and ceiling (R3, ADR-0033 ``ops``)

budget_app = typer.Typer(
    help=(
        "Cost ledger and budget ceiling (R3): set ceilings and the rate card, show "
        "remaining headroom, list and reconcile reservations."
    ),
    no_args_is_help=True,
)


def _budget_ledger(currency: str | None) -> BudgetLedger:
    """The ledger in ``currency`` (default: ``PLM_BUDGET_LEDGER_CURRENCY``) on the
    configured database."""
    from .db import build_engine, session_factory
    from .execution import BUDGET_LEDGER_CURRENCY_VAR, load_execution_settings

    resolved = currency or load_execution_settings(dotenv_path=REPO_ROOT / ".env").budget_currency
    if resolved is None:
        typer.echo(f"budget: pass --currency or set {BUDGET_LEDGER_CURRENCY_VAR}", err=True)
        raise typer.Exit(code=2)
    settings = load_settings(dotenv_path=REPO_ROOT / ".env")
    return BudgetLedger(session_factory(build_engine(settings)), currency=resolved)


def _instant(raw: str | None, option: str) -> datetime:
    """An ISO 8601 instant with an explicit offset; omitted means now (edge clock)."""
    if raw is None:
        return SystemClock().now()
    try:
        return require_aware(datetime.fromisoformat(raw))
    except ValueError as exc:
        typer.echo(f"budget: {option} must be an ISO 8601 instant with an offset ({exc})", err=True)
        raise typer.Exit(code=2) from exc


def _money_arg(amount: str, currency: str) -> Money:
    try:
        return Money.of(amount, currency.upper())
    except (ValueError, TypeError) as exc:
        typer.echo(f"budget: invalid amount {amount!r} {currency!r} ({exc})", err=True)
        raise typer.Exit(code=2) from exc


def _show(money: Money) -> str:
    return f"{money.amount} {money.currency}"


@contextmanager
def _budget_errors() -> Iterator[None]:
    from sqlalchemy.exc import SQLAlchemyError

    from .ledger import LedgerError

    try:
        yield
    except (LedgerError, ValueError, SQLAlchemyError) as exc:
        typer.echo(f"budget: refused - {_describe_for_cli(exc)}", err=True)
        raise typer.Exit(code=1) from exc


@budget_app.command(name="set-ceiling")
def budget_set_ceiling(
    amount: str = typer.Option(..., "--amount", help="Ceiling amount (decimal)"),
    currency: str = typer.Option(..., "--currency", help="ISO 4217; must be the ledger currency"),
    period_start: str = typer.Option(..., "--from", help="Period start, ISO 8601 with offset"),
    period_end: str = typer.Option(..., "--until", help="Period end (exclusive), ISO 8601"),
    set_by: str = typer.Option(..., "--set-by", help="Operator making the change"),
    capability: str | None = typer.Option(
        None, "--capability", help="Per-capability ceiling; omitted = the global ceiling"
    ),
    reference: str | None = typer.Option(None, "--reference", help="Ticket or approval ref"),
) -> None:
    """Create or change a ceiling for one period (global unless --capability)."""
    from .ledger import GLOBAL_SCOPE, capability_scope

    limit = _money_arg(amount, currency)
    ledger = _budget_ledger(limit.currency)
    with _budget_errors():
        ceiling = ledger.set_ceiling(
            limit=limit,
            period_start=_instant(period_start, "--from"),
            period_end=_instant(period_end, "--until"),
            scope=GLOBAL_SCOPE if capability is None else capability_scope(capability),
            set_by=set_by,
            reference=reference,
            now=SystemClock().now(),
        )
    typer.echo(
        f"budget: ceiling {ceiling.ceiling_id} = {_show(ceiling.limit)} until "
        f"{ceiling.period_end.isoformat()}"
    )


@budget_app.command(name="set-rate")
def budget_set_rate(
    capability: str = typer.Option(..., "--capability", help="e.g. youtube.upload"),
    amount: str = typer.Option(..., "--amount", help="Cost of one write (decimal; 0 = free)"),
    currency: str = typer.Option(..., "--currency", help="ISO 4217"),
    effective_from: str = typer.Option(..., "--from", help="Effective from, ISO 8601"),
    set_by: str = typer.Option(..., "--set-by", help="Operator making the change"),
    reference: str | None = typer.Option(None, "--reference", help="Price source"),
    ledger_currency: str | None = typer.Option(None, "--ledger-currency"),
) -> None:
    """Set the rate card entry that prices writes a caller cannot price itself."""
    cost = _money_arg(amount, currency)
    ledger = _budget_ledger(ledger_currency or cost.currency)
    with _budget_errors():
        rate = ledger.set_rate(
            capability=capability, cost=cost, effective_from=_instant(effective_from, "--from"),
            set_by=set_by, reference=reference, now=SystemClock().now(),
        )
    typer.echo(
        f"budget: rate {rate.capability} = {_show(rate.cost)} from "
        f"{rate.effective_from.isoformat()}"
    )


@budget_app.command(name="remaining")
def budget_remaining(
    capability: str | None = typer.Option(None, "--capability", help="Per-capability ceiling"),
    at: str | None = typer.Option(None, "--at", help="Instant to evaluate (default: now)"),
    currency: str | None = typer.Option(None, "--currency", help="Ledger currency override"),
) -> None:
    """Show the ceiling covering an instant: limit, reserved, committed and remaining."""
    from .ledger import GLOBAL_SCOPE, Reason, capability_scope

    scope = GLOBAL_SCOPE if capability is None else capability_scope(capability)
    instant = _instant(at, "--at")
    ledger = _budget_ledger(currency)
    with _budget_errors():
        status = ledger.ceiling_status(scope=scope, now=instant)
    if status is None:
        typer.echo(
            f"budget: no single {scope} ceiling covers {instant.isoformat()}; live writes are "
            f"refused ({Reason.NO_CEILING.value})",
            err=True,
        )
        raise typer.Exit(code=BLOCKED_EXIT_CODE)
    ceiling = status.ceiling
    typer.echo(
        f"{ceiling.scope} [{ceiling.period_start.isoformat()}, {ceiling.period_end.isoformat()})\n"
        f"  limit     {_show(ceiling.limit)}\n"
        f"  reserved  {_show(status.reserved)}\n"
        f"  committed {_show(status.committed)}\n"
        f"  remaining {_show(status.remaining)}"
    )


@budget_app.command(name="entries")
def budget_entries_command(
    status: str | None = typer.Option(None, "--status", help="reserved | committed | released"),
    capability: str | None = typer.Option(None, "--capability"),
    limit: int = typer.Option(100, "--limit"),
    currency: str | None = typer.Option(None, "--currency", help="Ledger currency override"),
) -> None:
    """List ledger entries, oldest first."""
    from .ledger import EntryStatus

    wanted = None
    if status is not None:
        try:
            wanted = EntryStatus(status.lower())
        except ValueError as exc:
            typer.echo(f"budget: unknown status {status!r}", err=True)
            raise typer.Exit(code=2) from exc
    ledger = _budget_ledger(currency)
    with _budget_errors():
        rows = ledger.entries(status=wanted, capability=capability, limit=limit)
    for entry in rows:
        actual = "-" if entry.actual is None else f"{_show(entry.actual)} ({entry.actual_basis})"
        typer.echo(
            f"{entry.reserved_at.isoformat()}  {entry.status.value:<9}  {entry.capability}  "
            f"{entry.idempotency_key}  reserved {_show(entry.reserved)} "
            f"[{entry.estimate_source}]  actual {actual}"
        )
    if not rows:
        typer.echo("budget: no entries")


@budget_app.command(name="commit")
def budget_commit(
    capability: str = typer.Argument(...),
    idempotency_key: str = typer.Argument(...),
    amount: str | None = typer.Option(None, "--amount", help="Actual cost; omitted = estimate"),
    actual_currency: str | None = typer.Option(None, "--actual-currency"),
    currency: str | None = typer.Option(None, "--currency", help="Ledger currency override"),
) -> None:
    """Reconcile a held reservation whose write did happen."""
    ledger = _budget_ledger(currency)
    actual = None
    if amount is not None:
        actual = _money_arg(amount, actual_currency or ledger.currency)
    with _budget_errors():
        entry = ledger.commit(capability, idempotency_key, actual=actual, now=SystemClock().now())
    assert entry.actual is not None
    typer.echo(f"budget: committed {capability}/{idempotency_key} at {_show(entry.actual)}")


@budget_app.command(name="release")
def budget_release(
    capability: str = typer.Argument(...),
    idempotency_key: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", help="Why the write did not happen"),
    currency: str | None = typer.Option(None, "--currency", help="Ledger currency override"),
) -> None:
    """Reconcile a held reservation whose write did not happen; frees its headroom."""
    ledger = _budget_ledger(currency)
    with _budget_errors():
        ledger.release(capability, idempotency_key, reason=reason, now=SystemClock().now())
    typer.echo(f"budget: released {capability}/{idempotency_key}")


app.add_typer(market_app, name="market")
app.add_typer(ar_app, name="ar")
app.add_typer(experiments_app, name="experiments")
app.add_typer(music_app, name="music")
app.add_typer(qa_app, name="qa")
app.add_typer(media_app, name="media")
app.add_typer(youtube_app, name="youtube")
app.add_typer(distribution_app, name="distribution")
app.add_typer(analytics_app, name="analytics")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(budget_app, name="budget")


def main() -> None:
    """Console-script entry point (``plm``)."""
    app()


if __name__ == "__main__":
    main()

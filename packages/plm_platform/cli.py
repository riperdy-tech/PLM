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
    head, MinIO reachability, Temporal reachability, and that the nine phase
    task-queue constants are intact. Prints a PASS/FAIL table and exits
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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

import typer
import urllib3
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from minio import Minio
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from temporalio.api.workflowservice.v1 import GetSystemInfoRequest

from .config import Settings, SettingsError, load_settings
from .db import database_url, redacted_database_url
from .storage import bucket_name, split_endpoint
from .temporal import Phase, TaskQueue, all_task_queues, connect_client

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
        for name in ("postgres", "migrations", "minio", "temporal"):
            checks.append(Check(name, False, "not checked - configuration failed to load"))
    else:
        checks.append(_postgres_check(settings, connect_timeout))
        checks.append(_migrations_check(settings, connect_timeout))
        checks.append(_minio_check(settings))
        checks.append(_temporal_check(settings, temporal_timeout))
    checks.append(_task_queues_check())
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


def _migrations_check(settings: Settings, connect_timeout: float) -> Check:
    try:
        head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    except Exception as exc:  # noqa: BLE001 - a broken checkout is a FAIL row too
        return Check(
            "migrations", False, f"alembic configuration unreadable - {_describe(exc)}"
        )
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
    if current == head:
        return Check("migrations", True, f"at {current} (head)")
    if current is None:
        return Check(
            "migrations", False, f"database not migrated (head is {head}) - run: plm migrate"
        )
    return Check(
        "migrations", False, f"database at {current}, head is {head} - run: plm migrate"
    )


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
    timeout = urllib3.Timeout(connect=_MINIO_PROBE_TIMEOUT_SECONDS, read=_MINIO_PROBE_TIMEOUT_SECONDS)
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
            "temporal", False, f"unreachable at {settings.temporal_host} - timed out after {timeout:g}s"
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
    if len(TaskQueue) == len(Phase) == len(expected) and actual == expected:
        return Check("task_queues", True, "9 phase queues intact (ph1.market .. ph9.analytics)")
    drift = [f"{want} != {got}" for want, got in zip(expected, actual) if want != got]
    if len(actual) != len(expected):
        drift.append(f"{len(actual)} queues, expected {len(expected)}")
    return Check("task_queues", False, "constants drifted: " + "; ".join(drift or ["unknown"]))


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
    """Wave 0 gate: PostgreSQL + migration head, MinIO, Temporal, task queues."""
    typer.echo(_DOCTOR_TITLE)
    checks = run_doctor()
    typer.echo(_render_table(checks))
    failures = [check for check in checks if not check.passed]
    if failures:
        typer.echo(f"doctor: {len(failures)} check(s) failed", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# migrate: the single alembic chain


@app.command()
def migrate(
    revision: str = typer.Argument("head", help="Target revision (default: head)."),
) -> None:
    """Upgrade the database along the alembic chain to REVISION (default: head)."""
    config = _alembic_config()
    try:
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
    typer.echo(reason, err=True)
    raise typer.Exit(code=BLOCKED_EXIT_CODE)


@ar_app.command()
def cycle(slot_count: int = 10, budget: float = 10.0, policy_version: str = "p2-v1",
          as_of: str = "") -> None:
    """Run one allocation cycle (P2)."""
    from autonomous_ar.cli import run_cycle_once

    typer.echo(json.dumps(run_cycle_once(slot_count=slot_count, budget=budget,
                                         policy_version=policy_version,
                                         as_of=as_of or None), indent=2, default=str))


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


@experiments_app.command()
def run() -> NoReturn:
    """Run a P3 experiment (registered stub until PHASE-P3)."""
    _not_implemented("PHASE-P3")


@music_app.command()
def generate() -> NoReturn:
    """Generate P4 music candidates (registered stub until PHASE-P4)."""
    _not_implemented("PHASE-P4")


@qa_app.command()
def review() -> NoReturn:
    """Work the P5 review queue (registered stub until PHASE-P5)."""
    _not_implemented("PHASE-P5")


@media_app.command()
def render() -> NoReturn:
    """Render a P6 media package (registered stub until PHASE-P6)."""
    _not_implemented("PHASE-P6")


@youtube_app.command()
def publish() -> NoReturn:
    """Publish to YouTube (registered stub until PHASE-P7)."""
    _not_implemented("PHASE-P7")


@distribution_app.command()
def submit() -> NoReturn:
    """Submit a P8 release to DSPs (registered stub until PHASE-P8)."""
    _not_implemented("PHASE-P8")


@analytics_app.command()
def ingest() -> NoReturn:
    """Ingest P9 analytics files (registered stub until PHASE-P9)."""
    _not_implemented("PHASE-P9")


app.add_typer(market_app, name="market")
app.add_typer(ar_app, name="ar")
app.add_typer(experiments_app, name="experiments")
app.add_typer(music_app, name="music")
app.add_typer(qa_app, name="qa")
app.add_typer(media_app, name="media")
app.add_typer(youtube_app, name="youtube")
app.add_typer(distribution_app, name="distribution")
app.add_typer(analytics_app, name="analytics")


def main() -> None:
    """Console-script entry point (``plm``)."""
    app()


if __name__ == "__main__":
    main()

"""Unit tests for the ``plm`` CLI (W0.12): surface, stubs, doctor, migrate.

Hermetic: the dead-endpoint doctor test injects every required variable and
points the dependencies at a closed port, so it never touches the dev stack
and never needs a ``.env`` file. (The against-the-stack green path lives in
``tests/integration/test_cli_doctor.py``.)
"""

from __future__ import annotations

from typing import Any

import pytest
from plm_platform import cli
from plm_platform.cli import NOT_IMPLEMENTED_EXIT_CODE, PHASES_BY_GROUP, Check, app
from typer.testing import CliRunner

runner = CliRunner()

STUB_CASES = [
    ("youtube", "publish", "PHASE-P7"),
    ("distribution", "submit", "PHASE-P8"),
    ("analytics", "ingest", "PHASE-P9"),
]

REQUIRED_ENV = {
    "POSTGRES_PASSWORD": "change-me",
    "DATABASE_URL": "postgresql://plm:change-me@127.0.0.1:9/plm",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "change-me-secret",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
}


def _output(result: Any) -> str:
    """Everything the command printed (typer's Result carries stdout+stderr in ``output``)."""
    return str(getattr(result, "output", "") or "")


def test_help_lists_doctor_migrate_and_every_phase_group() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("doctor", "migrate", *PHASES_BY_GROUP):
        assert name in result.output


@pytest.mark.parametrize("group", list(PHASES_BY_GROUP))
def test_every_group_has_a_discoverable_command(group: str) -> None:
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code == 0, _output(result)


@pytest.mark.parametrize(("group", "command", "phase"), STUB_CASES)
def test_stub_commands_exit_nonzero_with_the_phase_message(
    group: str, command: str, phase: str
) -> None:
    result = runner.invoke(app, [group, command])
    output = _output(result)
    assert result.exit_code == NOT_IMPLEMENTED_EXIT_CODE
    assert f"not implemented until {phase}" in output
    assert phase == PHASES_BY_GROUP[group]


def test_stub_cases_cover_every_registered_group() -> None:
    # P1's market group is implemented (P1.11), P2's ar group is implemented (P2.14),
    # P3's experiments group is implemented (P3.11), P4's music group is implemented (P4.13),
    # P5's qa group is implemented (P5.14), and P6's media group is implemented (P6.11),
    # so none of them are stubs.
    assert {group for group, _, _ in STUB_CASES} == set(PHASES_BY_GROUP) - {
        "market",
        "ar",
        "experiments",
        "music",
        "qa",
        "media",
    }


def test_market_commands_are_implemented_not_stubs() -> None:
    for command in ("collect", "score", "report", "run-daily", "serve", "smoke-live"):
        result = runner.invoke(app, ["market", command, "--help"])
        assert result.exit_code == 0, _output(result)
        assert "not implemented until" not in _output(result)


def test_ar_commands_are_implemented_not_stubs() -> None:
    for command in ("cycle", "show", "arms", "explain", "serve", "simulate"):
        result = runner.invoke(app, ["ar", command, "--help"])
        assert result.exit_code == 0, (command, _output(result))
        assert "not implemented until" not in _output(result)


def test_experiments_commands_are_implemented_not_stubs() -> None:
    for command in ("list", "show", "validate", "materialize", "serve", "smoke"):
        result = runner.invoke(app, ["experiments", command, "--help"])
        assert result.exit_code == 0, (command, _output(result))
        assert "not implemented until" not in _output(result)


def test_music_commands_are_implemented_not_stubs() -> None:
    for command in ("generate", "providers", "serve", "smoke"):
        result = runner.invoke(app, ["music", command, "--help"])
        assert result.exit_code == 0, (command, _output(result))
        assert "not implemented until" not in _output(result)


def test_doctor_reports_clean_fail_for_dead_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    result = runner.invoke(app, ["doctor"])
    output = _output(result)
    assert result.exit_code == 1
    lines = output.splitlines()
    for name in ("postgres", "minio", "temporal"):
        assert any(name in line and "FAIL" in line for line in lines), lines
    # the code-level check is unaffected by dead services
    assert any("task_queues" in line and "PASS" in line for line in lines)
    assert "Traceback" not in output
    # messages never leak configured secrets
    assert REQUIRED_ENV["POSTGRES_PASSWORD"] not in output
    assert REQUIRED_ENV["MINIO_ROOT_PASSWORD"] not in output


def test_doctor_exit_code_follows_the_check_table(monkeypatch: pytest.MonkeyPatch) -> None:
    passing = [Check("config", True, "ok"), Check("postgres", True, "ok")]
    monkeypatch.setattr(cli, "run_doctor", lambda: passing)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "2 checks: 2 PASS, 0 FAIL" in result.output

    failing = [*passing, Check("minio", False, "unreachable")]
    monkeypatch.setattr(cli, "run_doctor", lambda: failing)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "2 PASS, 1 FAIL" in result.output


def test_migrate_runs_the_alembic_chain_at_the_checkout_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str | None, str]] = []

    def fake_upgrade(config: Any, revision: str) -> None:
        calls.append((config.config_file_name, revision))

    monkeypatch.setattr(cli.command, "upgrade", fake_upgrade)
    first = runner.invoke(app, ["migrate"])
    second = runner.invoke(app, ["migrate", "0001_baseline"])
    assert first.exit_code == 0, _output(first)
    assert second.exit_code == 0, _output(second)
    expected_config = str(cli.REPO_ROOT / "alembic.ini")
    assert calls == [(expected_config, "head"), (expected_config, "0001_baseline")]


def test_migrate_failure_is_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_upgrade(config: Any, revision: str) -> None:
        raise RuntimeError("connection refused (simulated)")

    monkeypatch.setattr(cli.command, "upgrade", fake_upgrade)
    result = runner.invoke(app, ["migrate"])
    output = _output(result)
    assert result.exit_code == 1
    assert "simulated" in output
    assert "Traceback" not in output


def test_qa_subcommands_are_registered_and_display_help() -> None:
    res = runner.invoke(app, ["qa", "--help"])
    assert res.exit_code == 0
    assert "review" in res.output
    assert "evaluate" in res.output
    assert "manifest" in res.output
    assert "resolve" in res.output
    assert "serve" in res.output
    assert "smoke" in res.output


def test_media_subcommands_are_registered_and_display_help() -> None:
    res = runner.invoke(app, ["media", "--help"])
    assert res.exit_code == 0
    assert "assemble" in res.output
    assert "package" in res.output
    assert "serve" in res.output
    assert "smoke" in res.output

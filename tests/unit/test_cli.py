"""Unit tests for the ``plm`` CLI (W0.12): surface, stubs, doctor, migrate.

Hermetic: the dead-endpoint doctor test injects every required variable and
points the dependencies at a closed port, so it never touches the dev stack
and never needs a ``.env`` file. (The against-the-stack green path lives in
``tests/integration/test_cli_doctor.py``.)
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from plm_platform import cli
from plm_platform.cli import NOT_IMPLEMENTED_EXIT_CODE, PHASES_BY_GROUP, Check, app
from typer.testing import CliRunner

runner = CliRunner()

STUB_CASES: list[tuple[str, str, str]] = []

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
    for name in ("doctor", "migrate", "dashboard", *PHASES_BY_GROUP):
        assert name in result.output


def test_dashboard_cli_help() -> None:
    result = runner.invoke(app, ["dashboard", "--help"])
    assert result.exit_code == 0
    assert "dashboard" in result.output.lower()


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
    # P5's qa group is implemented (P5.14), P6's media group is implemented (P6.11),
    # and P7's youtube group is implemented (P7.11), P8's distribution group is implemented (P8.11), and P9's analytics group is implemented (P9.8), so none of them are stubs.
    assert {group for group, _, _ in STUB_CASES} == set(PHASES_BY_GROUP) - {
        "market",
        "ar",
        "experiments",
        "music",
        "qa",
        "media",
        "youtube",
        "distribution",
        "analytics",
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


def test_migration_check_refuses_unknown_revision_without_upgrade_advice() -> None:
    known = frozenset({"0037_dashboard_views", "0038_youtube_candidate_selection"})
    result = cli._migration_revision_result(
        "0038_release_reviews", "0038_youtube_candidate_selection", known
    )
    assert not result.passed
    assert "NOT in this checkout" in result.detail
    assert "do NOT run plm migrate" in result.detail
    assert "0038_release_reviews" in result.detail


def test_code_installed_merge_never_advises_unapproved_db_upgrade() -> None:
    known = frozenset({
        "0037_dashboard_views", "0038_release_reviews",
        "0038_youtube_candidate_selection", "0039_merge_0038_branches",
    })
    for current in (None, "0038_release_reviews", "0038_youtube_candidate_selection"):
        result = cli._migration_revision_result(
            current, "0039_merge_0038_branches", known,
        )
        assert not result.passed
        assert "do NOT run plm migrate" in result.detail
        assert "CODE ONLY" in result.detail
        hold = cli._merge_code_only_refusal("0039_merge_0038_branches", current)
        assert hold is not None
        assert "NOT approved" in hold
    assert cli._merge_code_only_refusal(
        "0039_merge_0038_branches", "0039_merge_0038_branches"
    ) is None
    assert cli._merge_code_only_refusal(
        "0038_youtube_candidate_selection", "0037_dashboard_views"
    ) is None


def test_new_0040_cost_migration_remains_code_only_on_local_0039_db() -> None:
    head = "0040_p4_unpriced_costs"
    current = "0039_merge_0038_branches"
    known = frozenset({current, head})
    check = cli._migration_revision_result(current, head, known)
    assert not check.passed
    assert "CODE ONLY" in check.detail
    assert "do NOT run plm migrate" in check.detail
    hold = cli._merge_code_only_refusal(head, current)
    assert hold is not None and "NOT approved" in hold
    assert cli._merge_code_only_refusal(head, head) is None


def test_new_0041_credit_migration_still_refuses_shared_0039_rollout() -> None:
    head = "0041_p4_credit_provenance"
    current = "0039_merge_0038_branches"
    known = frozenset({current, "0040_p4_unpriced_costs", head})
    result = cli._migration_revision_result(current, head, known)
    assert not result.passed
    assert "CODE ONLY" in result.detail
    assert "do NOT run plm migrate" in result.detail
    refusal = cli._merge_code_only_refusal(head, current)
    assert refusal is not None and "NOT approved" in refusal
    assert cli._merge_code_only_refusal(head, head) is None


def test_new_0042_render_evidence_migration_still_refuses_shared_rollout() -> None:
    head = "0042_media_render_evidence"
    current = "0039_merge_0038_branches"
    known = frozenset({current, "0040_p4_unpriced_costs", "0041_p4_credit_provenance", head})
    result = cli._migration_revision_result(current, head, known)
    assert not result.passed
    assert "CODE ONLY" in result.detail
    assert cli._merge_code_only_refusal(head, current) is not None
    assert cli._merge_code_only_refusal(head, head) is None


def test_actual_checkout_head_is_a_declared_code_only_head() -> None:
    """A new revision that becomes head must not silently lift the rollout refusal."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    heads = ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_heads()
    assert len(heads) == 1
    assert heads[0] in cli._CODE_ONLY_HEADS


def test_migration_check_distinguishes_known_pending_and_current_revisions() -> None:
    known = frozenset({"0037_dashboard_views", "0038_youtube_candidate_selection"})
    current = cli._migration_revision_result(
        "0038_youtube_candidate_selection", "0038_youtube_candidate_selection", known
    )
    assert current.passed
    previous = cli._migration_revision_result(
        "0037_dashboard_views", "0038_youtube_candidate_selection", known
    )
    assert not previous.passed
    assert "run: plm migrate" in previous.detail
    empty = cli._migration_revision_result(
        None, "0038_youtube_candidate_selection", known
    )
    assert not empty.passed
    assert "database not migrated" in empty.detail
    no_head = cli._migration_revision_result("0037_dashboard_views", None, known)
    assert not no_head.passed
    assert "no Alembic head" in no_head.detail
    assert "do NOT run plm migrate" in no_head.detail


@pytest.mark.parametrize(
    ("database_revision", "allowed"),
    [
        ("0038_release_reviews", False),
        ("0037_dashboard_views", True),
        ("0038_youtube_candidate_selection", True),
        (None, True),
    ],
)
def test_migrate_preflight_prevents_unknown_branch_before_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    database_revision: str | None,
    allowed: bool,
) -> None:
    upgrades: list[str] = []
    disposed: list[bool] = []
    script = SimpleNamespace(
        get_current_head=lambda: "0038_youtube_candidate_selection",
        walk_revisions=lambda: [
            SimpleNamespace(revision="0038_youtube_candidate_selection"),
            SimpleNamespace(revision="0037_dashboard_views"),
        ],
    )
    query_counts: list[int] = []

    def inspect_count(statement: Any) -> Any:
        query = str(statement)
        assert "pg_catalog.pg_namespace" in query
        assert "nspname NOT IN ('public', 'information_schema')" in query
        assert "pg_catalog.pg_proc" in query
        assert "pg_catalog.pg_type" in query
        assert "pg_catalog.pg_extension" in query
        query_counts.append(0)
        return SimpleNamespace(scalar_one=lambda: 0)

    engine = SimpleNamespace(
        connect=lambda: nullcontext(SimpleNamespace(execute=inspect_count)),
        dispose=lambda: disposed.append(True),
    )
    monkeypatch.setattr(cli.ScriptDirectory, "from_config", lambda config: script)
    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: object())
    monkeypatch.setattr(cli, "_engine", lambda settings, timeout: engine)
    monkeypatch.setattr(
        cli.MigrationContext,
        "configure",
        lambda connection: SimpleNamespace(get_current_revision=lambda: database_revision),
    )
    monkeypatch.setattr(cli, "_describe_for_cli", lambda exc: str(exc))
    monkeypatch.setattr(cli.command, "upgrade", lambda config, rev: upgrades.append(rev))
    result = runner.invoke(app, ["migrate"])
    assert disposed == [True]
    assert (result.exit_code == 0) is allowed
    assert upgrades == (["head"] if allowed else [])
    assert query_counts == ([0] if database_revision is None else [])
    if not allowed:
        assert "refusing migration" in _output(result)


def test_migrate_refuses_installed_0039_on_shared_database_without_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upgrades: list[str] = []
    closed: list[bool] = []
    script = SimpleNamespace(
        get_current_head=lambda: "0039_merge_0038_branches",
        walk_revisions=lambda: [
            SimpleNamespace(revision=rev)
            for rev in ("0037_dashboard_views", "0038_release_reviews",
                        "0038_youtube_candidate_selection", "0039_merge_0038_branches")
        ],
    )
    engine = SimpleNamespace(
        connect=lambda: nullcontext(SimpleNamespace()),
        dispose=lambda: closed.append(True),
    )
    monkeypatch.setattr(cli.ScriptDirectory, "from_config", lambda config: script)
    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: object())
    monkeypatch.setattr(cli, "_engine", lambda settings, timeout: engine)
    monkeypatch.setattr(
        cli.MigrationContext, "configure",
        lambda connection: SimpleNamespace(
            get_current_revision=lambda: "0038_release_reviews"
        ),
    )
    monkeypatch.setattr(cli.command, "upgrade", lambda config, revision: upgrades.append(revision))
    monkeypatch.setattr(cli, "_describe_for_cli", lambda exc: str(exc))
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 1
    assert "NOT approved" in _output(result)
    assert not upgrades
    assert closed == [True]


@pytest.mark.parametrize("object_count", [1, 12, None, -1, True, "0"])
def test_migrate_never_upgrades_an_unversioned_nonempty_or_unknown_database(
    monkeypatch: pytest.MonkeyPatch, object_count: object,
) -> None:
    upgrades: list[str] = []
    disposed: list[bool] = []
    script = SimpleNamespace(
        get_current_head=lambda: "0038_youtube_candidate_selection",
        walk_revisions=lambda: [
            SimpleNamespace(revision="0038_youtube_candidate_selection"),
        ],
    )

    def inspect_count(statement: Any) -> Any:
        sql = str(statement)
        assert "pg_catalog.pg_class" in sql
        assert "pg_catalog.pg_namespace" in sql
        assert "pg_catalog.pg_proc" in sql
        assert "pg_catalog.pg_type" in sql
        assert "pg_catalog.pg_extension" in sql
        return SimpleNamespace(scalar_one=lambda: object_count)

    engine = SimpleNamespace(
        connect=lambda: nullcontext(SimpleNamespace(execute=inspect_count)),
        dispose=lambda: disposed.append(True),
    )
    monkeypatch.setattr(cli.ScriptDirectory, "from_config", lambda config: script)
    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: object())
    monkeypatch.setattr(cli, "_engine", lambda settings, timeout: engine)
    monkeypatch.setattr(
        cli.MigrationContext, "configure",
        lambda connection: SimpleNamespace(get_current_revision=lambda: None),
    )
    monkeypatch.setattr(cli, "_describe_for_cli", lambda exc: str(exc))
    monkeypatch.setattr(cli.command, "upgrade", lambda config, rev: upgrades.append(rev))
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 1
    assert "unversioned" in _output(result)
    assert upgrades == []
    assert disposed == [True]


def test_migrate_refuses_failed_unversioned_catalog_read_before_alembic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upgrades: list[str] = []
    disposed: list[bool] = []
    script = SimpleNamespace(
        get_current_head=lambda: "0038_youtube_candidate_selection",
        walk_revisions=lambda: [
            SimpleNamespace(revision="0038_youtube_candidate_selection"),
        ],
    )

    def fail_read(statement: Any) -> Any:
        raise RuntimeError("catalog inspection unavailable")

    engine = SimpleNamespace(
        connect=lambda: nullcontext(SimpleNamespace(execute=fail_read)),
        dispose=lambda: disposed.append(True),
    )
    monkeypatch.setattr(cli.ScriptDirectory, "from_config", lambda config: script)
    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: object())
    monkeypatch.setattr(cli, "_engine", lambda settings, timeout: engine)
    monkeypatch.setattr(
        cli.MigrationContext, "configure",
        lambda connection: SimpleNamespace(get_current_revision=lambda: None),
    )
    monkeypatch.setattr(cli, "_describe_for_cli", lambda exc: str(exc))
    monkeypatch.setattr(cli.command, "upgrade", lambda config, rev: upgrades.append(rev))
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 1
    assert "catalog inspection unavailable" in _output(result)
    assert upgrades == []
    assert disposed == [True]


def test_migrate_refuses_unreachable_preflight_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upgrades: list[str] = []
    monkeypatch.setattr(
        cli, "_migration_preflight",
        lambda config: (_ for _ in ()).throw(RuntimeError("preflight failed")),
    )
    monkeypatch.setattr(cli, "_describe_for_cli", lambda exc: str(exc))
    monkeypatch.setattr(cli.command, "upgrade", lambda config, rev: upgrades.append(rev))
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 1
    assert "preflight failed" in _output(result)
    assert upgrades == []


def test_migrate_runs_the_alembic_chain_at_the_checkout_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str | None, str]] = []

    def fake_upgrade(config: Any, revision: str) -> None:
        calls.append((config.config_file_name, revision))

    monkeypatch.setattr(cli.command, "upgrade", fake_upgrade)
    monkeypatch.setattr(cli, "_migration_preflight", lambda config: None)
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
    monkeypatch.setattr(cli, "_migration_preflight", lambda config: None)
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


def test_youtube_subcommands_are_registered_and_display_help() -> None:
    res = runner.invoke(app, ["youtube", "--help"])
    assert res.exit_code == 0
    assert "publish" in res.output
    assert "attempt" in res.output
    assert "reconcile" in res.output
    assert "serve" in res.output
    assert "smoke" in res.output


def test_distribution_subcommands_are_registered_and_display_help() -> None:
    res = runner.invoke(app, ["distribution", "--help"])
    assert res.exit_code == 0
    assert "submit" in res.output
    assert "release" in res.output
    assert "report" in res.output
    assert "serve" in res.output
    assert "smoke" in res.output


def test_distribution_commands_are_implemented_not_stubs() -> None:
    for command in ("submit", "release", "report", "serve", "smoke"):
        result = runner.invoke(app, ["distribution", command, "--help"])
        assert result.exit_code == 0, (command, _output(result))
        assert "not implemented until" not in _output(result)


def test_analytics_subcommands_are_registered_and_display_help() -> None:
    res = runner.invoke(app, ["analytics", "--help"])
    assert res.exit_code == 0
    assert "ingest" in res.output
    assert "report" in res.output
    assert "rewards" in res.output
    assert "serve" in res.output
    assert "smoke" in res.output


def test_analytics_commands_are_implemented_not_stubs() -> None:
    for command in ("ingest", "report", "rewards", "serve", "smoke"):
        result = runner.invoke(app, ["analytics", command, "--help"])
        assert result.exit_code == 0, (command, _output(result))
        assert "not implemented until" not in _output(result)


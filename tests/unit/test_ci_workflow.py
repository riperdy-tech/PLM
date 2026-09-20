"""Guards for ``.github/workflows/ci.yml`` (W0.11).

CI cannot actually run in this repository without pushing to GitHub, so the
workflow is *parsed and audited* here instead: triggers, the pinned Python
version, the pip cache, the gate commands and their order, and — most
importantly — the W0.11 ruling that CI reuses the repository's own
``docker-compose.yml`` rather than declaring a parallel ``services:`` block.

These run in the fast suite (no services), so a workflow edit that violates
the ruling fails long before anyone pushes.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from tests.support import REPO_ROOT

WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _step_runs(job: dict[str, Any]) -> list[str]:
    return [step.get("run", "") for step in job["steps"]]


def _index_of(commands: list[str], needle: str) -> int:
    return next(index for index, command in enumerate(commands) if needle in command)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    parsed = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture(scope="module")
def job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert list(jobs) == ["ci"], "the plan asks for one job; keep it that way"
    return jobs["ci"]


def test_triggers_cover_push_and_pull_request(workflow: dict[str, Any]) -> None:
    # YAML 1.1 parses a bare `on:` key as boolean True; accept both spellings.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    assert set(triggers) == {"push", "pull_request"}


def test_runner_and_python_version(job: dict[str, Any]) -> None:
    assert job["runs-on"] == "ubuntu-latest"
    setup = next(step for step in job["steps"] if "setup-python" in step.get("uses", ""))
    assert setup["with"]["python-version"] == "3.12"


def test_no_parallel_service_definition(job: dict[str, Any]) -> None:
    """The W0.11 ruling: docker-compose.yml is the only service definition."""
    assert "services" not in job, (
        "the W0.11 ruling forbids a parallel service definition — start the "
        "repo's docker-compose.yml (docker compose up -d --wait) instead of "
        "declaring a GitHub services: block, or local/CI drift returns"
    )
    compose_runs = [run for run in _step_runs(job) if "docker compose" in run]
    assert compose_runs
    for run in compose_runs:
        assert " -f " not in run, (
            f"compose override would fork the service definition from the repo file: {run!r}"
        )


def test_gate_commands_present_and_in_order(job: dict[str, Any]) -> None:
    commands = _step_runs(job)
    joined = "\n".join(commands)
    assert 'pip install -e ".[dev]"' in joined
    assert "ruff check ." in joined
    assert "mypy packages/" in joined
    assert 'pytest -q -m "not integration"' in joined
    assert "docker compose up -d --wait" in joined
    assert "pytest -q -m integration" in joined
    lint = _index_of(commands, "ruff check .")
    fast = _index_of(commands, 'pytest -q -m "not integration"')
    up = _index_of(commands, "docker compose up -d")
    integration = _index_of(commands, "pytest -q -m integration")
    assert lint < fast < up < integration, "step order: lint, fast tests, containers, integration"


def test_stack_is_health_waited_before_integration(job: dict[str, Any]) -> None:
    up = next(run for run in _step_runs(job) if "docker compose up -d --wait" in run)
    assert "--wait-timeout" in up


def test_teardown_runs_even_after_failures(job: dict[str, Any]) -> None:
    steps = job["steps"]
    down = next(step for step in steps if "docker compose down" in step.get("run", ""))
    assert down.get("if") == "always()", "teardown must run even when tests failed"
    assert "-v" in down["run"], "teardown removes volumes so the next run starts clean"


def test_pip_download_cache_is_configured(job: dict[str, Any]) -> None:
    cache = next(step for step in job["steps"] if "actions/cache" in step.get("uses", ""))
    assert cache["with"]["path"] == "~/.cache/pip"
    assert "hashFiles('pyproject.toml')" in cache["with"]["key"]


def test_env_file_is_provided_from_the_committed_example(job: dict[str, Any]) -> None:
    joined = "\n".join(_step_runs(job))
    assert "cp .env.example .env" in joined, (
        "compose and the integration tests read .env; build it from the committed "
        "example rather than duplicating values in the workflow"
    )

"""Non-destructive guard tests for the user-approved isolated 0038 rehearsal."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.support import migration_isolated_rehearsal as rehearsal
from tests.support.migration_review_compare import MERGE, RELEASE


def test_rehearsal_requires_exact_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        rehearsal, "rehearse",
        lambda: pytest.fail("rehearsal ran without opt-in"),
    )
    assert rehearsal.main([]) == 2
    assert rehearsal.main(["--dry-run"]) == 2
    assert "refused" in capsys.readouterr().out


def test_rehearsal_never_leaks_exception_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def secret_failure() -> None:
        raise RuntimeError("PASSWORD=do-not-expose")

    monkeypatch.setattr(rehearsal, "rehearse", secret_failure)
    assert rehearsal.main([rehearsal.OPT_IN]) == 1
    result = capsys.readouterr().out
    assert "do-not-expose" not in result
    assert json.loads(result)["passed"] is False


@pytest.mark.parametrize("unsafe", ["plm", "postgres", "plm_migration_disposable_"])
def test_disposable_settings_refuse_unsafe_names_without_using_source(
    unsafe: str,
) -> None:
    with pytest.raises(RuntimeError, match="not disposable"):
        rehearsal._target_settings(None, 5433, "dummy-secret", unsafe)  # type: ignore[arg-type]


def test_snapshot_equality_requires_all_nonidentity_evidence() -> None:
    first: dict[str, Any] = {
        "database": "plm", "alembic_revision": RELEASE,
        "release_reviews": {"count": 2, "sha256": "a" * 64},
    }
    copied = {**first, "database": "plm_migration_disposable_clone"}
    assert rehearsal._same_preservation(first, copied)
    copied["release_reviews"] = {"count": 1, "sha256": "a" * 64}
    assert not rehearsal._same_preservation(first, copied)


def test_source_guard_rejects_unexpected_database_without_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rehearsal, "database_url",
        lambda _settings: SimpleNamespace(
            database="plm_migration_disposable_trial",
            host="localhost", port=5432, username="plm",
        ),
    )
    monkeypatch.setattr(
        rehearsal, "_inspect",
        lambda _name: pytest.fail("unexpected container inspection"),
    )
    with pytest.raises(RuntimeError, match="source identity"):
        rehearsal._source_guard(None)  # type: ignore[arg-type]


def test_existing_container_refused_without_running_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rehearsal, "_inspect", lambda _name: {"Name": "existing"})
    monkeypatch.setattr(
        rehearsal, "_run",
        lambda *args, **kwargs: pytest.fail("existing container was touched"),
    )
    with pytest.raises(RuntimeError, match="already occupied"):
        rehearsal._new_cluster("existing", "plm", "dummy-secret")


def test_alembic_never_runs_with_shared_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rehearsal, "_revision", lambda _settings: RELEASE)
    monkeypatch.setattr(
        rehearsal, "database_url",
        lambda _settings: SimpleNamespace(database="plm"),
    )
    monkeypatch.setattr(
        rehearsal.command, "upgrade",
        lambda *args: pytest.fail("Alembic executed against shared target"),
    )
    with pytest.raises(RuntimeError, match="unsafe Alembic target"):
        rehearsal._upgrade(
            None, None, RELEASE, MERGE,  # type: ignore[arg-type]
        )

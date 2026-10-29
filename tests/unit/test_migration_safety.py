"""A destructive Alembic round-trip is permitted only on a named throwaway DB.

These unit tests never open a DB or invoke migration commands.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from plm_platform.migration_safety import (
    code_only_0039_alembic_refusal,
    user_object_count,
)

from tests.support.migration_safety import (
    destructive_migration_name_refusal,
    destructive_migration_target_refusal,
)


@pytest.mark.parametrize(
    ("configured", "opted_in", "connected"),
    [
        (None, None, None),
        ("plm", None, "plm"),
        ("plm", "plm", "plm"),
        ("postgres", "postgres", "postgres"),
        ("plm_migration_disposable_", "plm_migration_disposable_", "plm_migration_disposable_"),
        ("PLM_migration_disposable_trial", "PLM_migration_disposable_trial", "PLM_migration_disposable_trial"),
        ("plm_migration_disposable_trial;", "plm_migration_disposable_trial;", "plm_migration_disposable_trial;"),
        ("plm_migration_disposable_trial", None, "plm_migration_disposable_trial"),
        ("plm_migration_disposable_trial", "plm", "plm_migration_disposable_trial"),
        ("plm_migration_disposable_trial", "plm_migration_disposable_trial", "plm"),
        ("plm_migration_disposable_trial", "plm_migration_disposable_trial", None),
    ],
)
def test_destructive_migration_target_refuses_unsafe_destinations(
    configured: str | None, opted_in: str | None, connected: str | None,
) -> None:
    reason = destructive_migration_target_refusal(configured, opted_in, connected, 0)
    assert reason is not None
    assert "password" not in reason


def test_destructive_migration_target_allows_only_exact_opted_in_throwaway_database() -> None:
    assert destructive_migration_target_refusal(
        "plm_migration_disposable_run42",
        "plm_migration_disposable_run42",
        "plm_migration_disposable_run42",
        0,
    ) is None


@pytest.mark.parametrize("count", [1, 17, -1, None])
def test_destructive_migration_target_rejects_nonempty_or_unverified_db(
    count: int | None,
) -> None:
    result = destructive_migration_target_refusal(
        "plm_migration_disposable_run42",
        "plm_migration_disposable_run42",
        "plm_migration_disposable_run42",
        count,
    )
    assert result is not None


def test_preconnection_migration_name_check_requires_explicit_disposable_optin() -> None:
    assert destructive_migration_name_refusal("plm", "plm") is not None
    assert destructive_migration_name_refusal("plm_r5_ci", "plm_r5_ci") is None
    assert destructive_migration_name_refusal(
        "plm_migration_disposable_run42", "plm_migration_disposable_run42"
    ) is None


@pytest.mark.parametrize("count", [0, 2, 47])
def test_shared_read_only_object_count_accepts_valid_catalog_evidence(count: int) -> None:
    inspected_sql: list[str] = []

    def execute(statement: object) -> SimpleNamespace:
        inspected_sql.append(str(statement))
        return SimpleNamespace(scalar_one=lambda: count)

    connection = SimpleNamespace(execute=execute)
    assert user_object_count(connection) == count
    assert len(inspected_sql) == 1
    for catalog in (
        "pg_catalog.pg_namespace", "pg_catalog.pg_class",
        "pg_catalog.pg_proc", "pg_catalog.pg_type", "pg_catalog.pg_extension",
    ):
        assert catalog in inspected_sql[0]
    assert "DELETE " not in inspected_sql[0].upper()
    assert "DROP " not in inspected_sql[0].upper()


@pytest.mark.parametrize("count", [None, -1, True, "0", 1.5])
def test_shared_read_only_object_count_rejects_unknown_evidence(count: object) -> None:
    connection = SimpleNamespace(
        execute=lambda statement: SimpleNamespace(scalar_one=lambda: count),
    )
    with pytest.raises(RuntimeError, match="cannot verify"):
        user_object_count(connection)


def test_shared_read_only_object_count_propagates_failed_catalog_inspection() -> None:
    def raise_on_query(statement: object) -> None:
        raise RuntimeError("catalog unavailable")

    connection = SimpleNamespace(execute=raise_on_query)
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        user_object_count(connection)


@pytest.mark.parametrize(
    ("configured", "connected", "host", "port"),
    [
        ("plm", "plm", "127.0.0.1", 5432),
        ("postgres", "postgres", "127.0.0.1", 5432),
        (None, None, "localhost", 55432),
        ("plm_migration_disposable_trial", "plm", "localhost", 55432),
        ("plm_migration_disposable_trial", None, "localhost", 55432),
        ("plm_migration_disposable_trial", "plm_migration_disposable_trial",
         "remote.example", 55432),
        ("plm_migration_disposable_trial", "plm_migration_disposable_trial",
         "127.0.0.1", 5432),
        ("plm_migration_disposable_", "plm_migration_disposable_", "localhost", 55432),
        ("PLM_migration_disposable_trial", "PLM_migration_disposable_trial",
         "localhost", 55432),
        ("plm_migration_disposable_trial", "plm_migration_disposable_trial",
         "localhost", True),
        ("plm_migration_disposable_trial", "plm_migration_disposable_trial",
         "localhost", None),
        ("plm_r5_ci", "plm_r5_ci", "127.0.0.1", 5432),
        ("plm_r5_", "plm_r5_", "127.0.0.1", 55432),
        ("plm_r5_ci", "plm", "127.0.0.1", 55432),
        ("plm_r5_ci", "plm_r5_ci", "::1", 55432),
    ],
)
def test_code_only_0039_direct_alembic_guard_rejects_unsafe_targets(
    configured: object, connected: object, host: object, port: object,
) -> None:
    refusal = code_only_0039_alembic_refusal(
        known_revisions=frozenset({
            "0038_release_reviews", "0038_youtube_candidate_selection",
            "0039_merge_0038_branches",
        }),
        configured_database=configured,
        connected_database=connected,
        host=host,
        port=port,
    )
    assert refusal is not None
    assert "code ONLY" in refusal
    assert "password" not in refusal.lower()


@pytest.mark.parametrize("target", ["plm_migration_disposable_rehearsal1", "plm_r5_ci"])
def test_code_only_0039_direct_alembic_guard_allows_only_isolated_test_target(
    target: str,
) -> None:
    assert code_only_0039_alembic_refusal(
        known_revisions=frozenset({"0039_merge_0038_branches"}),
        configured_database=target,
        connected_database=target,
        host="127.0.0.1",
        port=55432,
    ) is None


def test_direct_alembic_guard_survives_later_revision_head() -> None:
    assert code_only_0039_alembic_refusal(
        known_revisions=frozenset({"0039_merge_0038_branches", "0040_future"}),
        configured_database="plm",
        connected_database="plm",
        host="localhost",
        port=5432,
    ) is not None


def test_direct_alembic_guard_does_not_change_older_approved_chains() -> None:
    assert code_only_0039_alembic_refusal(
        known_revisions=frozenset({"0038_youtube_candidate_selection"}),
        configured_database="plm",
        connected_database="plm",
        host="localhost",
        port=5432,
    ) is None

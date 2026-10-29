"""Deterministic release-review row fingerprints; does not open PostgreSQL."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tests.support.migration_review_snapshot import (
    _EXPECTED_MANIFEST_INDEXES,
    manifest_common_schema_contract,
    manifest_index_definitions,
    manifest_integrity_contract,
    manifest_security_contract,
    manifest_storage_contract,
    review_access_contract,
    review_index_definitions,
    review_schema_contract,
    review_security_contract,
    review_storage_contract,
    selection_column_contract,
    table_fingerprint,
)


def test_manifest_security_digests_acl_and_owner_and_refuses_incomplete_catalog() -> None:
    row = {
        "table_name": "publish_manifests",
        "owner_name": "fixture-owner", "schema_owner_name": "schema-owner",
        "table_acl": "{role1=r/owner}", "schema_acl": None,
        "column_acl": '[[2,"{role1=w/owner}"]]',
        "schema_default_acl": None, "rls_enabled": False, "rls_forced": False,
        "custom_triggers": 0, "policies": 0,
    }
    actual = manifest_security_contract([row])
    assert len(actual["owner_sha256"]) == 64
    assert len(actual["table_acl_sha256"]) == 64
    assert actual["schema_acl_sha256"] is None
    assert "fixture-owner" not in str(actual)
    assert "role1" not in str(actual)
    assert manifest_security_contract([{**row, "table_acl": "{role2=r/owner}"}])[
        "table_acl_sha256"
    ] != actual["table_acl_sha256"]
    for bad in (
        [], [row, row], [{**row, "owner_name": None}],
        [{**row, "schema_owner_name": ""}],
        [{**row, "table_name": "wrong"}],
        [{**row, "column_acl": []}],
        [{**row, "rls_enabled": 0}],
        [{**row, "custom_triggers": True}],
        [{**row, "policies": -1}],
        [{**row, "unknown": "extra"}],
    ):
        with pytest.raises(ValueError, match="manifest security"):
            manifest_security_contract(bad)


def test_manifest_storage_rejects_missing_or_malformed_catalog_evidence() -> None:
    row = {
        "table_name": "publish_manifests", "relation_kind": "r",
        "persistence": "p", "replica_identity": "d",
        "is_partition": False, "rewrite_rules": 0,
    }
    assert manifest_storage_contract([row]) == {
        "relation_kind": "r", "persistence": "p", "replica_identity": "d",
        "is_partition": False, "rewrite_rules": 0,
    }
    for bad in (
        [], [row, row], [{**row, "table_name": "wrong"}],
        [{**row, "relation_kind": None}],
        [{**row, "persistence": "permanent"}],
        [{**row, "replica_identity": ""}],
        [{**row, "is_partition": 0}],
        [{**row, "rewrite_rules": True}],
        [{**row, "rewrite_rules": -1}],
        [{**row, "unexpected": "extra"}],
    ):
        with pytest.raises(ValueError, match="manifest storage"):
            manifest_storage_contract(bad)


def test_review_storage_requires_both_history_tables_or_neither() -> None:
    def record(table: str) -> dict:
        return {
            "table_name": table, "relation_kind": "r",
            "persistence": "p", "replica_identity": "d",
            "is_partition": False, "rewrite_rules": 0,
        }

    tables = [record("release_reviews"), record("release_review_events")]
    expected = {r["table_name"] for r in tables}
    actual = review_storage_contract(reversed(tables), has_reviews=True)
    assert actual is not None and set(actual) == expected
    assert all(item["persistence"] == "p" for item in actual.values())
    assert review_storage_contract([], has_reviews=False) is None

    for bad in (
        tables[:1], [tables[0], tables[0]],
        [*tables, record("unexpected")],
        [{**tables[0], "relation_kind": None}, tables[1]],
        [{**tables[0], "rewrite_rules": True}, tables[1]],
        [{**tables[0], "persistence": "unlogged"}, tables[1]],
    ):
        with pytest.raises(ValueError, match="storage"):
            review_storage_contract(bad, has_reviews=True)
    with pytest.raises(ValueError, match="storage"):
        review_storage_contract(tables, has_reviews=False)


def test_manifest_index_catalog_and_constraints_capture_fail_closed() -> None:
    rows = [{
        "index_name": name,
        "definition": "CREATE INDEX " + name,
        "is_primary": name == "publish_manifests_pkey",
        "is_valid": True, "is_ready": True, "is_live": True,
    } for name in sorted(_EXPECTED_MANIFEST_INDEXES)]
    observed = manifest_index_definitions(rows)
    assert set(observed) == _EXPECTED_MANIFEST_INDEXES
    assert observed["publish_manifests_pkey"]["is_primary"] is True

    class Inspector:
        def get_indexes(self, name: str, *, schema: str) -> list[dict]:
            assert name == "publish_manifests" and schema == "youtube"
            return [{"name": value} for value in sorted(
                _EXPECTED_MANIFEST_INDEXES - {"publish_manifests_pkey"}
            )]

        def get_check_constraints(self, name: str, *, schema: str) -> list[dict]:
            return []

        def get_foreign_keys(self, name: str, *, schema: str) -> list[dict]:
            return []

        def get_unique_constraints(self, name: str, *, schema: str) -> list[dict]:
            return []

    contract = manifest_integrity_contract(
        Inspector(), index_definitions=observed,
    )
    assert contract["check_constraints"] == []
    assert contract["index_definitions"] == observed
    for invalid in (
        rows[:-1], [*rows, rows[0]],
        [{**rows[0], "definition": None}, *rows[1:]],
        [{**rows[0], "is_ready": False}, *rows[1:]],
        [{**rows[0], "is_valid": 1}, *rows[1:]],
        [{**rows[0], "is_live": None}, *rows[1:]],
        [{**rows[0], "index_name": "unknown"}, *rows[1:]],
        [{**rows[0], "is_primary": True}, *rows[1:]],
    ):
        with pytest.raises(ValueError, match="manifest index|manifest primary"):
            manifest_index_definitions(invalid)

    class MissingIndexInspector(Inspector):
        def get_indexes(self, name: str, *, schema: str) -> list[dict]:
            return super().get_indexes(name, schema=schema)[:-1]

    with pytest.raises(ValueError, match="index catalog"):
        manifest_integrity_contract(
            MissingIndexInspector(), index_definitions=observed,
        )


def test_manifest_common_schema_records_columns_without_selection() -> None:
    columns = [
        {"name": "title", "type": "VARCHAR(255)", "nullable": False},
        {"name": "selection_json", "type": "JSON", "nullable": True},
        {"name": "manifest_id", "type": "VARCHAR(64)", "nullable": False},
    ]
    observed = manifest_common_schema_contract(
        columns, {"constrained_columns": ["manifest_id"]},
    )
    assert observed["primary_key"] == ["manifest_id"]
    assert [item["name"] for item in observed["columns"]] == ["manifest_id", "title"]
    assert observed["columns"][0]["server_default"] is None
    assert observed["columns"][0]["has_identity"] is False

    for invalid_columns, invalid_pk in (
        (columns, {"constrained_columns": ["title"]}),
        (columns + [columns[0]], {"constrained_columns": ["manifest_id"]}),
        ([{**columns[2], "nullable": 1}], {"constrained_columns": ["manifest_id"]}),
        ([{**columns[2], "default": 5}], {"constrained_columns": ["manifest_id"]}),
    ):
        with pytest.raises(ValueError, match="manifest common|primary key"):
            manifest_common_schema_contract(invalid_columns, invalid_pk)


def test_selection_column_contract_is_read_from_inspector_evidence() -> None:
    assert selection_column_contract([
        {"name": "manifest_id", "type": "VARCHAR", "nullable": False},
    ]) is None
    expected = {
        "type": "JSON", "nullable": True, "server_default": None,
        "has_computed": False, "has_identity": False,
    }
    assert selection_column_contract([
        {"name": "selection_json", "type": "JSON", "nullable": True},
    ]) == expected
    assert selection_column_contract([
        {"name": "selection_json", "type": "TEXT", "nullable": True},
    ]) == {**expected, "type": "TEXT"}
    assert selection_column_contract([{
        "name": "selection_json", "type": "JSON", "nullable": True,
        "default": "'{}'::json", "computed": {"sqltext": "'{}'::json"},
        "identity": {"always": True},
    }]) == {
        **expected, "server_default": "'{}'::json",
        "has_computed": True, "has_identity": True,
    }
    for invalid_default in (False, 0, ["invalid"]):
        with pytest.raises(ValueError, match="selection column schema"):
            selection_column_contract([{
                "name": "selection_json", "type": "JSON", "nullable": True,
                "default": invalid_default,
            }])
    with pytest.raises(ValueError):
        selection_column_contract([{"name": "selection_json", "type": "JSON"}])
    with pytest.raises(ValueError):
        selection_column_contract([
            {"name": "selection_json", "type": "JSON", "nullable": True},
            {"name": "selection_json", "type": "JSON", "nullable": True},
        ])


def test_review_access_contract_requires_complete_clean_acl_evidence() -> None:
    row = {
        "schema_name": "ops", "schema_acl_default": True,
        "schema_default_acl_entries": 0, "column_acl_entries": 0,
        "review_table_count": 2,
    }
    expected = {
        "schema_acl_default": True,
        "schema_default_acl_entries": 0,
        "column_acl_entries": 0,
    }
    assert review_access_contract([row], has_reviews=True) == expected
    assert review_access_contract([], has_reviews=False) is None
    for bad in (
        [], [row, row],
        [{**row, "schema_name": "other"}],
        [{**row, "schema_acl_default": 1}],
        [{**row, "schema_default_acl_entries": True}],
        [{**row, "column_acl_entries": -1}],
        [{**row, "review_table_count": 1}],
        [{**row, "schema_acl_default": None}],
        [{**row, "column_acl_entries": 0, "unexpected": 1}],
    ):
        with pytest.raises(ValueError):
            review_access_contract(bad, has_reviews=True)
    with pytest.raises(ValueError):
        review_access_contract([row], has_reviews=False)


def test_review_security_contract_captures_owner_digest_and_rejects_bad_catalog() -> None:
    rows = [{
        "table_name": name, "owner_name": "fixture-owner",
        "default_acl": True, "rls_enabled": False, "rls_forced": False,
        "custom_triggers": 0, "policies": 0,
    } for name in ("release_reviews", "release_review_events")]
    assert review_security_contract([]) is None
    actual = review_security_contract(rows)
    assert actual is not None
    assert actual["release_reviews"] == actual["release_review_events"]
    assert len(actual["release_reviews"]["owner_sha256"]) == 64
    assert "fixture-owner" not in str(actual)
    for bad in (
        rows[:1], [*rows, rows[0]],
        [{**rows[0], "owner_name": ""}, rows[1]],
        [{**rows[0], "default_acl": 1}, rows[1]],
        [{**rows[0], "custom_triggers": True}, rows[1]],
        [{**rows[0], "policies": -1}, rows[1]],
        [{**rows[0], "table_name": "unexpected"}, rows[1]],
    ):
        with pytest.raises(ValueError):
            review_security_contract(bad)


def test_review_index_definitions_preserve_full_ddl_and_fail_closed() -> None:
    row = {
        "table_name": "release_review_events",
        "index_name": "ix_release_review_events_package",
        "is_valid": True, "is_ready": True, "is_live": True,
        "definition": "CREATE INDEX ix_release_review_events_package ON "
                      "ops.release_review_events USING btree (package_id)",
    }
    expected = review_index_definitions([row])
    assert expected["release_reviews"] == {}
    assert expected["release_review_events"][row["index_name"]] == row["definition"]
    for bad_rows in (
        [row, row],
        [{**row, "definition": None}],
        [{**row, "is_valid": False}],
        [{**row, "is_ready": 1}],
        [{**row, "is_live": None}],
        [{**row, "index_name": ""}],
        [{**row, "table_name": "other_table"}],
    ):
        with pytest.raises(ValueError, match="index definition"):
            review_index_definitions(bad_rows)


def test_review_schema_contract_captures_defaults_and_constraints() -> None:
    class Inspector:
        def has_table(self, name: str, *, schema: str) -> bool:
            assert schema == "ops"
            return name in {"release_reviews", "release_review_events"}

        def get_columns(self, name: str, *, schema: str) -> list[dict]:
            return [{"name": "id", "type": "TEXT", "nullable": False,
                     "default": "'pending'::text" if name == "release_reviews" else None}]

        def get_pk_constraint(self, name: str, *, schema: str) -> dict:
            return {"constrained_columns": ["id"]}

        def get_indexes(self, name: str, *, schema: str) -> list[dict]:
            return []

        def get_check_constraints(self, name: str, *, schema: str) -> list[dict]:
            return ([{"name": "status_check", "sqltext": "id <> ''"}]
                    if name == "release_reviews" else [])

        def get_foreign_keys(self, name: str, *, schema: str) -> list[dict]:
            return ([{"name": "event_fk", "constrained_columns": ["id"],
                      "referred_schema": "ops", "referred_table": "release_reviews",
                      "referred_columns": ["id"]}]
                    if name == "release_review_events" else [])

        def get_unique_constraints(self, name: str, *, schema: str) -> list[dict]:
            return ([{"name": "review_unique", "column_names": ["id"]}]
                    if name == "release_reviews" else [])

    observed = review_schema_contract(
        Inspector(), index_definitions=review_index_definitions([]),
    )
    assert observed is not None
    reviews = observed["release_reviews"]
    assert reviews["columns"][0]["server_default"] == "'pending'::text"
    assert reviews["check_constraints"] == [
        {"name": "status_check", "sqltext": "id <> ''"}
    ]
    assert reviews["unique_constraints"] == [
        {"name": "review_unique", "columns": ["id"]}
    ]
    assert observed["release_review_events"]["foreign_keys"] == [{
        "name": "event_fk", "columns": ["id"], "referred_schema": "ops",
        "referred_table": "release_reviews", "referred_columns": ["id"],
    }]


def test_review_schema_contract_rejects_index_catalog_disagreement() -> None:
    class Inspector:
        def has_table(self, name: str, *, schema: str) -> bool:
            return True

        def get_columns(self, name: str, *, schema: str) -> list[dict]:
            return []

        def get_pk_constraint(self, name: str, *, schema: str) -> dict:
            return {"constrained_columns": ["id"]}

        def get_indexes(self, name: str, *, schema: str) -> list[dict]:
            return []

    indexes = review_index_definitions([{
        "table_name": "release_reviews",
        "index_name": "unreflected",
        "is_valid": True, "is_ready": True, "is_live": True,
        "definition": "CREATE INDEX unreflected ON ops.release_reviews (status)",
    }])
    with pytest.raises(ValueError, match="catalog differs"):
        review_schema_contract(Inspector(), index_definitions=indexes)


def test_review_schema_contract_refuses_uninspectable_server_default() -> None:
    class Inspector:
        def has_table(self, name: str, *, schema: str) -> bool:
            return True

        def get_columns(self, name: str, *, schema: str) -> list[dict]:
            return [{"name": "id", "type": "TEXT", "nullable": False, "default": 123}]

    with pytest.raises(ValueError, match="server default"):
        review_schema_contract(
            Inspector(), index_definitions=review_index_definitions([]),
        )


def test_review_snapshot_is_stable_across_row_order_column_order_and_tz() -> None:
    base = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    source = [
        {"package_id": "b", "title": "밤", "status": "approved",
         "created_at": base, "reason": None},
        {"package_id": "a", "status": "pending", "title": "Day",
         "created_at": base, "reason": None},
    ]
    reordered = [
        {"reason": None, "created_at": base.astimezone(timezone(timedelta(hours=9))),
         "title": "Day", "status": "pending", "package_id": "a"},
        {"created_at": base, "package_id": "b", "reason": None,
         "status": "approved", "title": "밤"},
    ]
    assert table_fingerprint(source, id_column="package_id") == table_fingerprint(
        reordered, id_column="package_id"
    )


def test_review_snapshot_detects_data_change_without_printing_rows() -> None:
    rows = [
        {"event_id": "e2", "reason": None, "event": "approved"},
        {"event_id": "e1", "reason": None, "event": "registered"},
    ]
    before = table_fingerprint(rows, id_column="event_id")
    after = table_fingerprint([
        rows[0], {"event_id": "e1", "reason": "changed", "event": "registered"}
    ], id_column="event_id")
    assert before["count"] == after["count"] == 2
    assert before["sha256"] != after["sha256"]
    assert "approved" not in str(before) and "changed" not in str(after)


@pytest.mark.parametrize("rows", [
    [{"package_id": "x"}, {"package_id": "x"}],
    [{"package_id": ""}],
    [{"unrelated": "x"}],
])
def test_review_snapshot_refuses_missing_or_repeated_ids(rows: list[dict]) -> None:
    with pytest.raises(ValueError, match="missing or duplicate"):
        table_fingerprint(rows, id_column="package_id")


def test_review_snapshot_refuses_truncated_digest() -> None:
    with pytest.raises(ValueError, match="row limit"):
        table_fingerprint(
            [{"package_id": str(i)} for i in range(3)],
            id_column="package_id", max_rows=2,
        )


def test_youtube_common_columns_match_when_nullable_0038_column_is_added() -> None:
    old = [{"manifest_id": "m1", "title": "Night", "status": "private"}]
    new = [{**old[0], "selection_json": None}]
    common_before = table_fingerprint(old, id_column="manifest_id")
    common_after = table_fingerprint(
        [{k: v for k, v in row.items() if k != "selection_json"} for row in new],
        id_column="manifest_id",
    )
    assert common_before == common_after
    expected_null_selection_before = table_fingerprint(
        [{"manifest_id": row["manifest_id"], "selection_json": None} for row in old],
        id_column="manifest_id",
    )
    expected_null_selection_after = table_fingerprint(
        [{"manifest_id": row["manifest_id"], "selection_json": None} for row in new],
        id_column="manifest_id",
    )
    actual_new_column = table_fingerprint(
        [{"manifest_id": row["manifest_id"], "selection_json": row["selection_json"]}
         for row in new],
        id_column="manifest_id",
    )
    assert expected_null_selection_before == expected_null_selection_after
    assert actual_new_column == expected_null_selection_before


def test_youtube_selection_fingerprint_detects_changed_assignment() -> None:
    assigned = [{"manifest_id": "m1", "selection_json": {"candidate_id": "a"}}]
    changed = [{"manifest_id": "m1", "selection_json": {"candidate_id": "b"}}]
    assert table_fingerprint(assigned, id_column="manifest_id") != table_fingerprint(
        changed, id_column="manifest_id"
    )


def test_review_snapshot_refuses_naive_timestamps_and_unserializable_values() -> None:
    for value in (datetime(2026, 10, 25), object()):
        with pytest.raises(ValueError):
            table_fingerprint(
                [{"package_id": "a", "timestamp": value}], id_column="package_id"
            )

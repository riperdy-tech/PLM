"""No-database comparison tests for the proposed 0038 -> 0039 migration."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.support.migration_review_compare import (
    _EXPECTED_MANIFEST_INDEX_DEFINITIONS,
    _EXPECTED_MANIFEST_SCHEMA,
    _EXPECTED_REVIEWS,
    MERGE,
    RELEASE,
    YOUTUBE,
    compare_preservation,
    main,
)
from tests.support.migration_review_snapshot import _EXPECTED_MANIFEST_INDEXES, table_fingerprint


def _fp(id_col: str, rows: list[dict]) -> dict[str, str | int]:
    return table_fingerprint(rows, id_column=id_col)


def _security() -> dict:
    return {
        table: {
            "owner_sha256": "a" * 64,
            "default_acl": True,
            "rls_enabled": False,
            "rls_forced": False,
            "custom_triggers": 0,
            "policies": 0,
        }
        for table in ("release_reviews", "release_review_events")
    }


def _review_storage() -> dict:
    return {name: _manifest_storage() for name in ("release_reviews", "release_review_events")}


def _access() -> dict:
    return {
        "schema_acl_default": True,
        "schema_default_acl_entries": 0,
        "column_acl_entries": 0,
    }


def _selection_schema() -> dict:
    return {
        "type": "JSON", "nullable": True, "server_default": None,
        "has_computed": False, "has_identity": False,
    }


def _manifest_schema() -> dict:
    return copy.deepcopy(_EXPECTED_MANIFEST_SCHEMA)


def _manifest_integrity() -> dict:
    return {
        "index_definitions": {
            name: {
                "definition": _EXPECTED_MANIFEST_INDEX_DEFINITIONS[name],
                "is_primary": name == "publish_manifests_pkey",
            }
            for name in sorted(_EXPECTED_MANIFEST_INDEXES)
        },
        "check_constraints": [],
        "foreign_keys": [],
        "unique_constraints": [],
    }


def _manifest_security() -> dict:
    return {
        "owner_sha256": "a" * 64,
        "schema_owner_sha256": "b" * 64,
        "table_acl_sha256": None,
        "schema_acl_sha256": "c" * 64,
        "column_acl_sha256": None,
        "schema_default_acl_sha256": None,
        "rls_enabled": False,
        "rls_forced": False,
        "custom_triggers": 0,
        "policies": 0,
    }


def _manifest_storage() -> dict:
    return {
        "relation_kind": "r", "persistence": "p",
        "replica_identity": "d", "is_partition": False,
        "rewrite_rules": 0,
    }


def _cases(start: str) -> tuple[dict, dict, dict]:
    reviews = _fp("package_id", [{"package_id": "p", "status": "approved"}])
    events = _fp("event_id", [{"event_id": "e", "package_id": "p"}])
    common = _fp("manifest_id", [
        {"manifest_id": "m1", "title": "Original"},
        {"manifest_id": "m2", "title": "Second"},
    ])
    selection = _fp("manifest_id", [
        {"manifest_id": "m1", "selection_json": {"choice": "A"}},
        {"manifest_id": "m2", "selection_json": None},
    ])
    null_selection = _fp("manifest_id", [
        {"manifest_id": "m1", "selection_json": None},
        {"manifest_id": "m2", "selection_json": None},
    ])
    source = {
        "database": "plm",
        "alembic_revision": start,
        "release_reviews": reviews if start == RELEASE else None,
        "release_review_events": events if start == RELEASE else None,
        "release_review_schema": copy.deepcopy(_EXPECTED_REVIEWS) if start == RELEASE else None,
        "release_review_security": _security() if start == RELEASE else None,
        "release_review_access": _access() if start == RELEASE else None,
        "youtube_manifest_common": common,
        "youtube_manifest_schema": _manifest_schema(),
        "youtube_manifest_integrity": _manifest_integrity(),
        "youtube_manifest_security": _manifest_security(),
        "youtube_manifest_storage": _manifest_storage(),
        "youtube_selection_column_present": start == YOUTUBE,
        "youtube_selection_column_contract": (
            _selection_schema() if start == YOUTUBE else None
        ),
        "youtube_selection": selection if start == YOUTUBE else None,
        "youtube_all_null_selection": null_selection,
        "youtube_selection_nonnull": 1 if start == YOUTUBE else None,
        "read_only": True,
        "transaction_isolation": "repeatable_read",
        "release_review_storage": _review_storage() if start == RELEASE else None,
    }
    before = {**source, "database": "plm_migration_disposable_clonex",
              "youtube_manifest_schema": _manifest_schema(),
              "youtube_manifest_integrity": _manifest_integrity(),
              "youtube_manifest_security": _manifest_security(),
              "youtube_manifest_storage": _manifest_storage()}
    after = {
        **before,
        "alembic_revision": MERGE,
        "youtube_manifest_schema": _manifest_schema(),
        "youtube_manifest_integrity": _manifest_integrity(),
        "youtube_manifest_security": _manifest_security(),
        "youtube_manifest_storage": _manifest_storage(),
        "release_reviews": reviews if start == RELEASE else _fp("package_id", []),
        "release_review_events": events if start == RELEASE else _fp("event_id", []),
        "release_review_schema": copy.deepcopy(_EXPECTED_REVIEWS),
        "release_review_storage": _review_storage(),
        "release_review_security": _security(),
        "release_review_access": _access(),
        "youtube_selection_column_present": True,
        "youtube_selection_column_contract": _selection_schema(),
        "youtube_selection": null_selection if start == RELEASE else selection,
        "youtube_selection_nonnull": 0 if start == RELEASE else 1,
    }
    return source, before, after


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_valid_preservation_evidence_only_passes_for_approved_clone_shape(start: str) -> None:
    source, before, after = _cases(start)
    assert compare_preservation(source, before, after) == {"passed": True, "failures": []}


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("field", "value"), [
        ("relation_kind", "p"),
        ("persistence", "u"),
        ("persistence", "t"),
        ("replica_identity", "f"),
        ("replica_identity", "n"),
        ("is_partition", True),
        ("is_partition", 0),
        ("rewrite_rules", 1),
        ("rewrite_rules", True),
        ("relation_kind", None),
    ],
)
def test_manifest_storage_drift_fails_even_with_identical_row_digests(
    start: str, field: str, value: object,
) -> None:
    source, before, after = _cases(start)
    after["youtube_manifest_storage"][field] = value
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_manifest_storage_identical_unsafe_source_and_clone_are_rejected(start: str) -> None:
    source, before, after = _cases(start)
    for report in (source, before, after):
        report["youtube_manifest_storage"]["persistence"] = "u"
    assert compare_preservation(source, before, after)["passed"] is False
    source, before, after = _cases(start)
    before.pop("youtube_manifest_storage")
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("field", "value"),
    [("relation_kind", "v"), ("persistence", "u"), ("persistence", "t"),
     ("replica_identity", "f"), ("is_partition", True),
     ("rewrite_rules", 1), ("rewrite_rules", False)],
)
def test_review_history_storage_drift_is_rejected(
    start: str, field: str, value: object,
) -> None:
    source, before, after = _cases(start)
    after["release_review_storage"]["release_review_events"][field] = value
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_review_storage_missing_evidence_is_rejected(start: str) -> None:
    source, before, after = _cases(start)
    after["release_review_storage"].pop("release_review_events")
    assert compare_preservation(source, before, after)["passed"] is False
    source, before, after = _cases(start)
    source.pop("release_review_storage")
    assert compare_preservation(source, before, after)["passed"] is False


def test_manifest_historical_reference_includes_all_29_pre_0038_columns() -> None:
    columns = _EXPECTED_MANIFEST_SCHEMA["columns"]
    assert len(columns) == 29
    assert [col["name"] for col in columns] == sorted(col["name"] for col in columns)
    names = {col["name"] for col in columns}
    assert {"tracks_json", "tags", "created_at", "publish_at", "idempotency_key"} <= names
    assert "selection_json" not in names
    assert next(col for col in columns if col["name"] == "tracks_json")["nullable"] is True
    assert next(col for col in columns if col["name"] == "category_id")[
        "server_default"
    ] == "'10'::character varying"


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    "drift",
    [
        "removed_column", "added_column", "wrong_type", "wrong_default",
        "missing_default", "wrong_timezone", "wrong_nullability",
        "generated_column", "boolean_spoof", "wrong_primary_key",
    ],
)
def test_consistently_forged_historical_manifest_schema_is_refused(
    start: str, drift: str,
) -> None:
    source, before, after = _cases(start)
    for report in (source, before, after):
        schema = report["youtube_manifest_schema"]
        columns = schema["columns"]
        by_name = {col["name"]: col for col in columns}
        if drift == "removed_column":
            columns.remove(by_name["tags"])
        elif drift == "added_column":
            columns.append({**by_name["tags"], "name": "invented_column"})
        elif drift == "wrong_type":
            by_name["video_duration_seconds"]["type"] = "TEXT"
        elif drift == "wrong_default":
            by_name["category_id"]["server_default"] = "'11'::character varying"
        elif drift == "missing_default":
            by_name["made_for_kids"]["server_default"] = None
        elif drift == "wrong_timezone":
            by_name["created_at"]["timezone"] = False
        elif drift == "wrong_nullability":
            by_name["tracks_json"]["nullable"] = False
        elif drift == "generated_column":
            by_name["title"]["has_computed"] = True
        elif drift == "boolean_spoof":
            by_name["manifest_id"]["nullable"] = 0
        else:
            schema["primary_key"] = ["title"]
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("owner_sha256", "d" * 64),
        ("schema_owner_sha256", "d" * 64),
        ("table_acl_sha256", "d" * 64),
        ("schema_acl_sha256", None),
        ("column_acl_sha256", "e" * 64),
        ("schema_default_acl_sha256", "f" * 64),
        ("rls_enabled", True),
        ("rls_forced", True),
        ("custom_triggers", 1),
        ("policies", 1),
        ("policies", True),
        ("table_acl_sha256", "invalid"),
    ],
)
def test_manifest_security_drift_refused_despite_matching_rows(
    start: str, field: str, bad_value: object,
) -> None:
    source, before, after = _cases(start)
    after["youtube_manifest_security"][field] = bad_value
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_manifest_security_missing_or_forged_clone_refused(start: str) -> None:
    source, before, after = _cases(start)
    after.pop("youtube_manifest_security")
    assert compare_preservation(source, before, after)["passed"] is False
    source, before, after = _cases(start)
    before["youtube_manifest_security"]["owner_sha256"] = "f" * 64
    after["youtube_manifest_security"]["owner_sha256"] = "f" * 64
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    "drift", ["wrong_column", "missing_unique", "wrong_method", "extra_predicate"],
)
def test_identically_tampered_historical_index_ddl_is_refused(
    start: str, drift: str,
) -> None:
    source, before, after = _cases(start)
    for report in (source, before, after):
        indexes = report["youtube_manifest_integrity"]["index_definitions"]
        key = "ix_youtube_publish_manifests_idempotency_key"
        if drift == "wrong_column":
            indexes["publish_manifests_pkey"]["definition"] = (
                _EXPECTED_MANIFEST_INDEX_DEFINITIONS["publish_manifests_pkey"]
                .replace("(manifest_id)", "(channel_id)")
            )
        elif drift == "missing_unique":
            indexes[key]["definition"] = indexes[key]["definition"].replace(
                "CREATE UNIQUE INDEX", "CREATE INDEX"
            )
        elif drift == "wrong_method":
            indexes[key]["definition"] = indexes[key]["definition"].replace(
                "USING btree", "USING hash"
            )
        else:
            indexes[key]["definition"] += " WHERE idempotency_key IS NOT NULL"
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("section", "alteration"),
    [
        ("index_definitions", "method"),
        ("index_definitions", "missing"),
        ("index_definitions", "invalid_primary"),
        ("check_constraints", "extra"),
        ("foreign_keys", "extra"),
        ("unique_constraints", "extra"),
    ],
)
def test_manifest_indexes_and_constraints_drift_is_refused(
    start: str, section: str, alteration: str,
) -> None:
    source, before, after = _cases(start)
    integrity = after["youtube_manifest_integrity"]
    if section == "index_definitions":
        indexes = integrity["index_definitions"]
        name = "ix_youtube_publish_manifests_idempotency_key"
        if alteration == "method":
            indexes[name]["definition"] += " USING hash (idempotency_key)"
        elif alteration == "missing":
            indexes.pop(name)
        else:
            indexes["publish_manifests_pkey"]["is_primary"] = False
    else:
        integrity[section].append({"name": "unexpected"})
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_manifest_integrity_missing_or_forged_clone_fails(start: str) -> None:
    source, before, after = _cases(start)
    before["youtube_manifest_integrity"]["index_definitions"].pop(
        "publish_manifests_pkey"
    )
    assert compare_preservation(source, before, after)["passed"] is False
    source, before, after = _cases(start)
    after.pop("youtube_manifest_integrity")
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("part", "value"),
    [
        ("type", "TEXT"),
        ("nullable", True),
        ("server_default", "'changed'::text"),
        ("has_computed", True),
        ("has_identity", True),
    ],
)
def test_manifest_common_column_schema_drift_refused_with_equal_row_hashes(
    start: str, part: str, value: object,
) -> None:
    source, before, after = _cases(start)
    after["youtube_manifest_schema"]["columns"][1][part] = value
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_manifest_common_schema_missing_or_changed_clone_refused(start: str) -> None:
    source, before, after = _cases(start)
    before["youtube_manifest_schema"]["columns"].pop()
    assert compare_preservation(source, before, after)["passed"] is False
    source, before, after = _cases(start)
    after["youtube_manifest_schema"]["primary_key"] = ["title"]
    assert compare_preservation(source, before, after)["passed"] is False
    source, before, after = _cases(start)
    after.pop("youtube_manifest_schema")
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("schema_acl_default", False),
        ("schema_acl_default", 1),
        ("schema_default_acl_entries", 1),
        ("schema_default_acl_entries", True),
        ("column_acl_entries", 1),
        ("column_acl_entries", -1),
    ],
)
def test_review_access_refuses_unexpected_schema_or_column_privileges(
    start: str, field: str, invalid: object,
) -> None:
    source, before, after = _cases(start)
    after["release_review_access"][field] = invalid
    assert compare_preservation(source, before, after)["passed"] is False


def test_review_first_rejects_changed_clone_schema_acl() -> None:
    source, before, after = _cases(RELEASE)
    before["release_review_access"] = _access()
    before["release_review_access"]["column_acl_entries"] = 1
    after["release_review_access"] = before["release_review_access"].copy()
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_review_access_missing_metadata_refused(start: str) -> None:
    source, before, after = _cases(start)
    after.pop("release_review_access")
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("default_acl", False),
        ("default_acl", 1),
        ("rls_enabled", True),
        ("rls_forced", True),
        ("custom_triggers", 1),
        ("custom_triggers", True),
        ("policies", 1),
        ("owner_sha256", "not-a-digest"),
    ],
)
def test_review_security_fails_closed_on_unsafe_metadata(
    start: str, field: str, bad_value: object,
) -> None:
    source, before, after = _cases(start)
    after["release_review_security"]["release_reviews"][field] = bad_value
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_review_security_missing_or_owner_mismatch_refused(start: str) -> None:
    source, before, after = _cases(start)
    after["release_review_security"]["release_review_events"]["owner_sha256"] = "b" * 64
    assert compare_preservation(source, before, after)["passed"] is False
    source, before, after = _cases(start)
    after.pop("release_review_security")
    assert compare_preservation(source, before, after)["passed"] is False


def test_review_first_refuses_clone_owner_change_despite_matching_row_digests() -> None:
    source, before, after = _cases(RELEASE)
    before["release_review_security"] = copy.deepcopy(source["release_review_security"])
    before["release_review_security"]["release_reviews"]["owner_sha256"] = "b" * 64
    before["release_review_security"]["release_review_events"]["owner_sha256"] = "b" * 64
    after["release_review_security"] = copy.deepcopy(before["release_review_security"])
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize(
    ("which", "field", "new_value"),
    [
        ("source", "alembic_revision", "0037_dashboard_views"),
        ("before", "database", "plm"),
        ("after", "database", "plm_migration_disposable_other"),
        ("after", "alembic_revision", RELEASE),
        ("source", "read_only", False),
        ("after", "transaction_isolation", "read_committed"),
        ("before", "youtube_manifest_common", None),
        ("after", "youtube_manifest_common", None),
        ("after", "youtube_selection_column_present", False),
        ("after", "youtube_selection_nonnull", 1),
        ("before", "release_reviews", None),
        ("after", "release_review_events", None),
        ("before", "youtube_manifest_common", {"count": True, "sha256": "f" * 64}),
    ],
)
def test_review_first_refuses_corrupt_or_changed_evidence(
    which: str, field: str, new_value: object,
) -> None:
    cases = list(_cases(RELEASE))
    edited = {"source": 0, "before": 1, "after": 2}[which]
    cases[edited][field] = new_value
    result = compare_preservation(*cases)
    assert result["passed"] is False
    assert result["failures"]


@pytest.mark.parametrize(
    ("which", "field", "new_value"),
    [
        ("before", "release_reviews", {"count": 0, "sha256": "a" * 64}),
        ("after", "release_review_events", {"count": 1, "sha256": "c" * 64}),
        ("after", "youtube_selection", None),
        ("after", "youtube_selection_nonnull", 0),
        ("before", "youtube_selection_column_present", False),
    ],
)
def test_youtube_first_refuses_unexpected_ops_or_selection_changes(
    which: str, field: str, new_value: object,
) -> None:
    cases = list(_cases(YOUTUBE))
    cases[{"source": 0, "before": 1, "after": 2}[which]][field] = new_value
    assert compare_preservation(*cases)["passed"] is False


@pytest.mark.parametrize("invalid_count", [3, -1, "2", None])
def test_youtube_first_rejects_selection_count_exceeding_manifest_rows(
    invalid_count: object,
) -> None:
    source, before, after = _cases(YOUTUBE)
    # Keep all three snapshots consistent: old equality-only checks allowed
    # impossible selection counts to appear preserved.
    for report in (source, before, after):
        report["youtube_selection_nonnull"] = invalid_count
    assert compare_preservation(source, before, after)["passed"] is False


def test_youtube_first_rejects_selection_digest_row_count_mismatch() -> None:
    source, before, after = _cases(YOUTUBE)
    for report in (source, before, after):
        report["youtube_selection"] = {
            "count": 1, "sha256": "a" * 64,
        }
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize(
    ("table", "section", "mutate"),
    [
        ("release_review_events", "indexes", "remove"),
        ("release_review_events", "indexes", "wrong_column"),
        ("release_reviews", "primary_key", "remove"),
        ("release_reviews", "columns", "wrong_type"),
        ("release_reviews", "columns", "nullable"),
        ("release_review_events", "columns", "missing"),
    ],
)
def test_review_schema_drift_refused_even_when_row_fingerprints_unchanged(
    table: str, section: str, mutate: str,
) -> None:
    source, before, after = _cases(RELEASE)
    schema = after["release_review_schema"][table]
    if section == "indexes":
        if mutate == "remove":
            schema["indexes"].clear()
        else:
            schema["indexes"][0]["columns"] = ["event_id"]
    elif section == "primary_key":
        schema["primary_key"].clear()
    elif section == "columns":
        if mutate == "missing":
            schema["columns"].pop()
        elif mutate == "wrong_type":
            schema["columns"][0]["type"] = "TEXT"
        else:
            schema["columns"][0]["nullable"] = True
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("unique", 0),
        ("unique", "false"),
    ],
)
def test_index_boolean_impersonation_cannot_match_reference(
    field: str, wrong_value: object,
) -> None:
    source, before, after = _cases(YOUTUBE)
    after["release_review_schema"]["release_review_events"]["indexes"][0][field] = (
        wrong_value
    )
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize(
    "index_ddl",
    [
        ("CREATE INDEX ix_release_review_events_package ON "
        "ops.release_review_events USING hash (package_id)"),
        ("CREATE INDEX ix_release_review_events_package ON "
        "ops.release_review_events USING btree (package_id DESC)"),
        ("CREATE INDEX ix_release_review_events_package ON "
        "ops.release_review_events USING btree (package_id) INCLUDE (event_id)"),
        ("CREATE INDEX ix_release_review_events_package ON "
        "ops.release_review_events USING btree (package_id) WHERE (event = 'approved')"),
        ("CREATE INDEX ix_release_review_events_package ON "
        "ops.release_review_events USING btree (lower(package_id))"),
        ("CREATE INDEX ix_release_review_events_package ON "
        "ops.release_review_events USING btree (package_id text_pattern_ops)"),
    ],
)
def test_full_index_ddl_change_fails_without_row_changes(index_ddl: str) -> None:
    name = "ix_release_review_events_package"
    source, before, after = _cases(RELEASE)
    after["release_review_schema"]["release_review_events"]["index_definitions"][
        name
    ] = index_ddl
    assert compare_preservation(source, before, after)["passed"] is False

    source, before, after = _cases(RELEASE)
    source["release_review_schema"]["release_review_events"]["index_definitions"][
        name
    ] = index_ddl
    before["release_review_schema"] = copy.deepcopy(source["release_review_schema"])
    assert compare_preservation(source, before, after)["passed"] is False


def test_column_nullable_integer_impostor_cannot_match_reference() -> None:
    source, before, after = _cases(RELEASE)
    after["release_review_schema"]["release_reviews"]["columns"][0]["nullable"] = 0
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize(
    ("table", "section", "value"),
    [
        ("release_reviews", "server_default", "'approved'::text"),
        ("release_review_events", "check_constraints",
         [{"name": "event_check", "sqltext": "event <> ''"}]),
        ("release_review_events", "foreign_keys",
         [{"name": "event_fk", "columns": ["package_id"],
           "referred_schema": "ops", "referred_table": "release_reviews",
           "referred_columns": ["package_id"]}]),
        ("release_reviews", "unique_constraints",
         [{"name": "status_unique", "columns": ["status"]}]),
    ],
)
def test_review_schema_unexpected_defaults_or_constraints_fail_closed(
    table: str, section: str, value: object,
) -> None:
    source, before, after = _cases(RELEASE)
    if section == "server_default":
        after["release_review_schema"][table]["columns"][0][section] = value
    else:
        after["release_review_schema"][table][section] = value
    assert compare_preservation(source, before, after)["passed"] is False

    # A defective historical source is also invalid, even if its clone matches.
    source, before, after = _cases(RELEASE)
    if section == "server_default":
        source["release_review_schema"][table]["columns"][0][section] = value
        before["release_review_schema"] = copy.deepcopy(source["release_review_schema"])
    else:
        source["release_review_schema"][table][section] = value
        before["release_review_schema"] = copy.deepcopy(source["release_review_schema"])
    assert compare_preservation(source, before, after)["passed"] is False


def test_youtube_first_review_schema_requires_exact_creation() -> None:
    source, before, after = _cases(YOUTUBE)
    assert compare_preservation(source, before, after)["passed"] is True
    after["release_review_schema"]["release_review_events"]["indexes"][0]["unique"] = True
    assert compare_preservation(source, before, after)["passed"] is False


def test_missing_review_structure_on_review_first_source_refused() -> None:
    source, before, after = _cases(RELEASE)
    source["release_review_schema"] = None
    before["release_review_schema"] = None
    assert compare_preservation(source, before, after)["passed"] is False


def test_empty_table_requires_canonical_empty_digest() -> None:
    source, before, after = _cases(YOUTUBE)
    fake_empty = {"count": 0, "sha256": "f" * 64}
    for report in (source, before, after):
        report["youtube_manifest_common"] = fake_empty
        report["youtube_selection"] = fake_empty
        report["youtube_selection_nonnull"] = 0
    assert compare_preservation(source, before, after)["passed"] is False


def test_review_first_rejects_false_all_null_claim_for_new_column() -> None:
    source, before, after = _cases(RELEASE)
    # An attacker could formerly pair zero reported non-NULL values with an
    # arbitrary *well-formed* digest and the comparator would pass.
    after["youtube_selection"] = {
        "count": before["youtube_manifest_common"]["count"],
        "sha256": "f" * 64,
    }
    result = compare_preservation(source, before, after)
    assert result["passed"] is False
    assert any("expected all-NULL digest" in failure for failure in result["failures"])


def test_all_null_expected_digest_must_be_stable_and_complete() -> None:
    source, before, after = _cases(RELEASE)
    assert compare_preservation(source, before, after)["passed"]
    before["youtube_all_null_selection"] = {
        "count": 1, "sha256": "a" * 64,
    }
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_missing_all_null_reference_never_passes(start: str) -> None:
    source, before, after = _cases(start)
    for report in (source, before, after):
        report.pop("youtube_all_null_selection")
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize(
    ("starting_revision", "wrong_schema"),
    [
        (RELEASE, {"type": "TEXT", "nullable": True}),
        (RELEASE, {"type": "JSONB", "nullable": True}),
        (RELEASE, {"type": "JSON", "nullable": False}),
        (RELEASE, {"type": "JSON", "nullable": 1}),
        (RELEASE, None),
        (YOUTUBE, {"type": "TEXT", "nullable": True}),
        (YOUTUBE, {"type": "JSON", "nullable": False}),
        (YOUTUBE, None),
    ],
)
def test_wrong_column_type_or_nullability_fails_closed(
    starting_revision: str, wrong_schema: object,
) -> None:
    source, before, after = _cases(starting_revision)
    after["youtube_selection_column_contract"] = wrong_schema
    assert compare_preservation(source, before, after)["passed"] is False


def test_review_first_rejects_schema_claim_for_absent_source_column() -> None:
    source, before, after = _cases(RELEASE)
    source["youtube_selection_column_contract"] = _selection_schema()
    before["youtube_selection_column_contract"] = _selection_schema()
    assert compare_preservation(source, before, after)["passed"] is False


def test_youtube_first_rejects_invalid_source_column_contract() -> None:
    source, before, after = _cases(YOUTUBE)
    source["youtube_selection_column_contract"] = {**_selection_schema(), "type": "TEXT"}
    before["youtube_selection_column_contract"] = {**_selection_schema(), "type": "TEXT"}
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("server_default", "'{}'::json"),
        ("server_default", False),
        ("has_computed", True),
        ("has_computed", 1),
        ("has_identity", True),
        ("has_identity", None),
    ],
)
def test_selection_default_or_generation_drift_fails_closed(
    start: str, field: str, value: object,
) -> None:
    source, before, after = _cases(start)
    after["youtube_selection_column_contract"][field] = value
    assert compare_preservation(source, before, after)["passed"] is False

    if start == YOUTUBE:
        source, before, after = _cases(start)
        source["youtube_selection_column_contract"][field] = value
        before["youtube_selection_column_contract"] = copy.deepcopy(
            source["youtube_selection_column_contract"]
        )
        after["youtube_selection_column_contract"][field] = value
        assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("start", [RELEASE, YOUTUBE])
def test_selection_contract_missing_default_fails_closed(start: str) -> None:
    source, before, after = _cases(start)
    after["youtube_selection_column_contract"].pop("server_default")
    assert compare_preservation(source, before, after)["passed"] is False


def test_missing_selection_digest_is_not_consistent_with_present_column() -> None:
    source, before, after = _cases(RELEASE)
    after["youtube_selection"] = None
    assert compare_preservation(source, before, after)["passed"] is False


@pytest.mark.parametrize("field", [
    "release_reviews", "release_review_events", "youtube_selection",
    "youtube_selection_nonnull", "youtube_selection_column_present",
])
def test_youtube_first_missing_nullable_fields_are_not_valid_evidence(
    field: str,
) -> None:
    source, before, after = _cases(YOUTUBE)
    source.pop(field)
    before.pop(field)
    assert compare_preservation(source, before, after)["passed"] is False


def test_extra_snapshot_fields_fail_closed() -> None:
    source, before, after = _cases(RELEASE)
    after["unrecognized_evidence"] = "accepted"
    assert compare_preservation(source, before, after)["passed"] is False


def test_snapshot_digest_count_exceeding_bounded_collector_is_rejected() -> None:
    source, before, after = _cases(YOUTUBE)
    for report in (source, before, after):
        report["youtube_manifest_common"] = {
            "count": 10001, "sha256": "f" * 64,
        }
        report["youtube_selection"] = {
            "count": 10001, "sha256": "a" * 64,
        }
    assert compare_preservation(source, before, after)["passed"] is False


def test_source_and_clone_identity_must_be_distinct() -> None:
    source, before, after = _cases(RELEASE)
    source["database"] = before["database"]
    assert compare_preservation(source, before, after)["passed"] is False


def test_absent_keys_never_count_as_valid_preservation_evidence() -> None:
    source, before, after = _cases(RELEASE)
    for field in ("youtube_manifest_common", "youtube_selection_column_present"):
        damaged = copy.deepcopy(after)
        damaged.pop(field)
        assert compare_preservation(source, before, damaged)["passed"] is False


def test_offline_compare_cli_reads_only_existing_digest_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source, before, after = _cases(RELEASE)
    paths = [tmp_path / f"{label}.json" for label in ("source", "before", "after")]
    for path, report in zip(paths, (source, before, after), strict=True):
        path.write_text(json.dumps(report), encoding="utf-8")
    assert main([str(p) for p in paths]) == 0
    assert json.loads(capsys.readouterr().out) == {"passed": True, "failures": []}
    after["youtube_selection_nonnull"] = 2
    paths[2].write_text(json.dumps(after), encoding="utf-8")
    assert main([str(p) for p in paths]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["passed"] is False
    assert "Original" not in str(output)


@pytest.mark.parametrize(
    "invalid_json",
    [
        '{"database":"plm","database":"forged"}',
        '{"database":NaN}',
        '{"database":Infinity}',
        "[]",
    ],
)
def test_offline_comparator_cli_rejects_ambiguous_or_nonfinite_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], invalid_json: str,
) -> None:
    source, before, after = _cases(RELEASE)
    paths = [tmp_path / f"{name}.json" for name in ("src", "before", "after")]
    for path, content in zip(paths, (source, before, after), strict=True):
        path.write_text(json.dumps(content), encoding="utf-8")
    paths[0].write_text(invalid_json, encoding="utf-8")
    assert main([str(p) for p in paths]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_offline_comparator_cli_rejects_oversized_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source, before, after = _cases(RELEASE)
    paths = [tmp_path / f"{name}.json" for name in ("src", "before", "after")]
    for path, content in zip(paths, (source, before, after), strict=True):
        path.write_text(json.dumps(content), encoding="utf-8")
    paths[2].write_bytes(b" " * 65000)
    assert main([str(p) for p in paths]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_offline_compare_cli_refuses_missing_files(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["not_here.json", "another.json", "third.json"]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False

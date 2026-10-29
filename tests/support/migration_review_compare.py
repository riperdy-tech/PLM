"""Offline fail-closed comparison of 0038 migration preservation fingerprints.

Consumes digest-only JSON made by migration_review_snapshot; never connects to
PostgreSQL, creates a database, or invokes Alembic. This is evidence checking,
not authorization to execute an upgrade.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from tests.support.migration_review_snapshot import _EXPECTED_MANIFEST_INDEXES
from tests.support.migration_safety import destructive_migration_name_refusal

RELEASE = "0038_release_reviews"
YOUTUBE = "0038_youtube_candidate_selection"
MERGE = "0039_merge_0038_branches"
MAX_SNAPSHOT_BYTES = 64_000
_HEX64 = re.compile(r"\A[a-f0-9]{64}\Z")
_EMPTY = {"count": 0, "sha256": sha256(b"").hexdigest()}


def _column(
    name: str, type_name: str, nullable: bool, *, timezone: bool | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": name, "type": type_name, "nullable": nullable,
        "server_default": None,
    }
    if timezone is not None:
        value["timezone"] = timezone
    return value


# Exact subset defined by the SHA256-pinned historic 0038 review revision.
_EXPECTED_REVIEWS = {
    "release_reviews": {
        "columns": sorted([
            _column("package_id", "VARCHAR(64)", False),
            _column("title", "TEXT", False),
            _column("video_ref", "TEXT", True),
            _column("thumbnail_ref", "TEXT", True),
            _column("status", "VARCHAR(16)", False),
            _column("reason", "TEXT", True),
            _column("video_id", "VARCHAR(32)", True),
            _column("created_at", "TIMESTAMP", False, timezone=True),
            _column("decided_at", "TIMESTAMP", True, timezone=True),
        ], key=lambda col: col["name"]),
        "primary_key": ["package_id"],
        "indexes": [],
        "index_definitions": {},
        "check_constraints": [],
        "foreign_keys": [],
        "unique_constraints": [],
    },
    "release_review_events": {
        "columns": sorted([
            _column("event_id", "VARCHAR(36)", False),
            _column("package_id", "VARCHAR(64)", False),
            _column("event", "VARCHAR(24)", False),
            _column("reason", "TEXT", True),
            _column("occurred_at", "TIMESTAMP", False, timezone=True),
        ], key=lambda col: col["name"]),
        "primary_key": ["event_id"],
        "indexes": [{
            "name": "ix_release_review_events_package",
            "columns": ["package_id"],
            "unique": False,
        }],
        "index_definitions": {
            "ix_release_review_events_package": (
                "CREATE INDEX ix_release_review_events_package ON "
                "ops.release_review_events USING btree (package_id)"
            ),
        },
        "check_constraints": [],
        "foreign_keys": [],
        "unique_constraints": [],
    },
}


def _manifest_column(
    name: str, type_name: str, *, nullable: bool = False,
    server_default: str | None = None, timezone: bool | None = None,
) -> dict[str, Any]:
    return {
        "name": name, "type": type_name, "nullable": nullable,
        "server_default": server_default, "timezone": timezone,
        "has_computed": False, "has_identity": False,
    }


# PostgreSQL pg_get_indexdef() output for 0013's original indexes.
# Known-good DDL is checked independently of source/clone agreement.
_MANIFEST_INDEX_COLUMNS = {
    "publish_manifests_pkey": ("manifest_id", True),
    "ix_youtube_publish_manifests_channel_id": ("channel_id", False),
    "ix_youtube_publish_manifests_experiment_id": ("experiment_id", False),
    "ix_youtube_publish_manifests_idempotency_key": ("idempotency_key", True),
    "ix_youtube_publish_manifests_media_package_id": ("media_package_id", False),
    "ix_youtube_publish_manifests_release_manifest_id": ("release_manifest_id", False),
}
_EXPECTED_MANIFEST_INDEX_DEFINITIONS = {
    name: (
        f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} "
        f"ON youtube.publish_manifests USING btree ({column})"
    )
    for name, (column, unique) in _MANIFEST_INDEX_COLUMNS.items()
}


# Frozen pre-0038 table definition: migration 0013 plus tracks_json from
# migration 0033, independently of the observed source database.
_EXPECTED_MANIFEST_SCHEMA = {
    "columns": sorted([
        *(
            _manifest_column(name, "VARCHAR(64)")
            for name in (
                "brand_id", "channel_id", "content_arm_id", "content_genome_id",
                "experiment_family_id", "experiment_id", "idempotency_key",
                "manifest_id", "media_package_id", "production_directive_id",
                "release_manifest_id", "thumbnail_checksum", "video_checksum",
            )
        ),
        _manifest_column("playlist_id", "VARCHAR(64)", nullable=True),
        _manifest_column(
            "category_id", "VARCHAR(16)",
            server_default="'10'::character varying",
        ),
        _manifest_column(
            "metadata_version", "VARCHAR(32)",
            server_default="'v1'::character varying",
        ),
        _manifest_column("privacy_status", "VARCHAR(32)"),
        _manifest_column("playlist_title", "VARCHAR(255)", nullable=True),
        _manifest_column("title", "VARCHAR(255)"),
        _manifest_column("contains_synthetic_media", "BOOLEAN", server_default="true"),
        _manifest_column("made_for_kids", "BOOLEAN", server_default="false"),
        _manifest_column("created_at", "TIMESTAMP", timezone=True),
        _manifest_column("publish_at", "TIMESTAMP", nullable=True, timezone=True),
        _manifest_column("description", "TEXT"),
        _manifest_column("thumbnail_uri", "TEXT"),
        _manifest_column("video_master_uri", "TEXT"),
        _manifest_column("tags", "JSON"),
        _manifest_column("tracks_json", "JSON", nullable=True),
        _manifest_column("video_duration_seconds", "DOUBLE PRECISION"),
    ], key=lambda col: col["name"]),
    "primary_key": ["manifest_id"],
}


_FINGERPRINTS = ("release_reviews", "release_review_events", "youtube_manifest_common",
                 "youtube_selection")
_REQUIRED_FIELDS = frozenset({
    "database", "alembic_revision", "read_only", "transaction_isolation",
    "release_reviews", "release_review_events", "release_review_schema",
    "release_review_security", "release_review_access", "youtube_manifest_common",
    "youtube_manifest_schema", "youtube_manifest_integrity",
    "youtube_manifest_security", "youtube_manifest_storage",
    "release_review_storage",
    "youtube_selection_column_present", "youtube_selection",
    "youtube_selection_nonnull", "youtube_all_null_selection",
    "youtube_selection_column_contract",
})


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and type(value.get("count")) is int
        and 0 <= value["count"] <= 10000
        and isinstance(value.get("sha256"), str)
        and _HEX64.fullmatch(value["sha256"]) is not None
        and set(value) == {"count", "sha256"}
    )


def _valid_selection_contract(value: object) -> bool:
    """Require exact, typed PostgreSQL JSON/nullable evidence."""
    return (
        type(value) is dict
        and set(value) == {
            "type", "nullable", "server_default", "has_computed", "has_identity",
        }
        and type(value["type"]) is str
        and value["type"] == "JSON"
        and value["nullable"] is True
        and value["server_default"] is None
        and value["has_computed"] is False
        and value["has_identity"] is False
    )


def _valid_manifest_schema(value: object) -> bool:
    """Require exact historical 29-column schema, not just self-consistent evidence.

    JSON serialisation is type-sensitive (True != 1) and rejects non-finite
    values, extra/missing columns, altered defaults and generated columns.
    """
    if type(value) is not dict:
        return False
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ) == json.dumps(
            _EXPECTED_MANIFEST_SCHEMA,
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def _valid_manifest_integrity(value: object) -> bool:
    """Require original index names, valid primary-key evidence and no new constraints."""
    if type(value) is not dict or set(value) != {
        "index_definitions", "check_constraints", "foreign_keys",
        "unique_constraints",
    }:
        return False
    indexes = value["index_definitions"]
    if type(indexes) is not dict or set(indexes) != _EXPECTED_MANIFEST_INDEXES:
        return False
    for name, detail in indexes.items():
        if (
            type(detail) is not dict
            or set(detail) != {"definition", "is_primary"}
            or type(detail["definition"]) is not str
            or detail["definition"] != _EXPECTED_MANIFEST_INDEX_DEFINITIONS.get(name)
            or detail["is_primary"] is not (name == "publish_manifests_pkey")
        ):
            return False
    return all(
        type(value[field]) is list and value[field] == []
        for field in ("check_constraints", "foreign_keys", "unique_constraints")
    )


def _valid_manifest_security(value: object) -> bool:
    """Require typed ownership/grant digests and safe manifest execution controls."""
    if type(value) is not dict or set(value) != {
        "owner_sha256", "schema_owner_sha256", "table_acl_sha256",
        "schema_acl_sha256", "column_acl_sha256", "schema_default_acl_sha256",
        "rls_enabled", "rls_forced", "custom_triggers", "policies",
    }:
        return False
    for field in ("owner_sha256", "schema_owner_sha256"):
        if type(value[field]) is not str or _HEX64.fullmatch(value[field]) is None:
            return False
    for field in (
        "table_acl_sha256", "schema_acl_sha256",
        "column_acl_sha256", "schema_default_acl_sha256",
    ):
        digest = value[field]
        if digest is not None and (
            type(digest) is not str or _HEX64.fullmatch(digest) is None
        ):
            return False
    return (
        value["rls_enabled"] is False
        and value["rls_forced"] is False
        and type(value["custom_triggers"]) is int
        and value["custom_triggers"] == 0
        and type(value["policies"]) is int
        and value["policies"] == 0
    )


def _valid_manifest_storage(value: object) -> bool:
    """Reject unlogged, temporary, partitioned or rewritten manifest relations."""
    return (
        type(value) is dict
        and set(value) == {
            "relation_kind", "persistence", "replica_identity",
            "is_partition", "rewrite_rules",
        }
        and value["relation_kind"] == "r"
        and type(value["relation_kind"]) is str
        and value["persistence"] == "p"
        and type(value["persistence"]) is str
        and value["replica_identity"] == "d"
        and type(value["replica_identity"]) is str
        and value["is_partition"] is False
        and type(value["rewrite_rules"]) is int
        and value["rewrite_rules"] == 0
    )


def _valid_review_storage(value: object) -> bool:
    """Both historical approval tables must be ordinary permanent relations."""
    return (
        type(value) is dict
        and set(value) == {"release_reviews", "release_review_events"}
        and all(_valid_manifest_storage(detail) for detail in value.values())
    )


def _valid_review_schema(value: object) -> bool:
    """Compare JSON structure *and* scalar types, not Python's True == 1."""
    if type(value) is not dict:
        return False
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ) == json.dumps(
            _EXPECTED_REVIEWS, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def _valid_review_security(value: object) -> bool:
    """Require complete owner/grant/trigger/RLS evidence, without revealing role IDs."""
    tables = {"release_reviews", "release_review_events"}
    fields = {
        "owner_sha256", "default_acl", "rls_enabled", "rls_forced",
        "custom_triggers", "policies",
    }
    if type(value) is not dict or set(value) != tables:
        return False
    owners: set[str] = set()
    for detail in value.values():
        if type(detail) is not dict or set(detail) != fields:
            return False
        owner = detail["owner_sha256"]
        if type(owner) is not str or _HEX64.fullmatch(owner) is None:
            return False
        owners.add(owner)
        if (
            detail["default_acl"] is not True
            or detail["rls_enabled"] is not False
            or detail["rls_forced"] is not False
            or type(detail["custom_triggers"]) is not int
            or detail["custom_triggers"] != 0
            or type(detail["policies"]) is not int
            or detail["policies"] != 0
        ):
            return False
    return len(owners) == 1


def _valid_review_access(value: object) -> bool:
    return (
        type(value) is dict
        and set(value) == {
            "schema_acl_default", "schema_default_acl_entries",
            "column_acl_entries",
        }
        and value["schema_acl_default"] is True
        and type(value["schema_default_acl_entries"]) is int
        and value["schema_default_acl_entries"] == 0
        and type(value["column_acl_entries"]) is int
        and value["column_acl_entries"] == 0
    )


def compare_preservation(
    source: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless source -> disposable clone -> merged clone retains all data.

    The source/before snapshots must be gathered at clone time. Missing review
    tables are legitimate only on the YouTube-first branch. The selection
    column is legitimate only on that branch, and must remain unchanged.
    """
    failures: list[str] = []

    def require(condition: bool, reason: str) -> None:
        if not condition:
            failures.append(reason)

    original_rev = source.get("alembic_revision")
    require(original_rev in (RELEASE, YOUTUBE), "unsupported source revision")
    clone_name = before.get("database")
    require(
        isinstance(clone_name, str)
        and destructive_migration_name_refusal(clone_name, clone_name) is None,
        "before snapshot is not from a named disposable database",
    )
    require(
        isinstance(source.get("database"), str)
        and source["database"] != clone_name
        and source["database"] != after.get("database"),
        "source and clone database identities are not distinct",
    )
    require(after.get("database") == clone_name, "before/after clone database differs")
    require(before.get("alembic_revision") == original_rev,
            "clone starting revision differs from source")
    require(after.get("alembic_revision") == MERGE,
            "clone has not reached the reviewed merge revision")

    for label, report in (("source", source), ("before", before), ("after", after)):
        require(set(report) == _REQUIRED_FIELDS,
                f"{label} snapshot has missing or unexpected fields")
        require(report.get("read_only") is True and
                report.get("transaction_isolation") == "repeatable_read",
                f"{label} snapshot was not captured as repeatable-read read-only")
        for field in _FINGERPRINTS:
            value = report.get(field)
            require(value is None or _valid_digest(value),
                    f"{label} {field} fingerprint missing or malformed")
            if _valid_digest(value) and value["count"] == 0:
                require(value == _EMPTY, f"{label} {field} empty-table hash is invalid")
        review_schema = report.get("release_review_schema")
        require(
            _valid_review_storage(report.get("release_review_storage"))
            if review_schema is not None
            else report.get("release_review_storage") is None,
            f"{label} review table storage evidence invalid",
        )
        if review_schema is not None:
            require(_valid_review_schema(review_schema),
                    f"{label} review tables/index/constraints differ from reference")
        security = report.get("release_review_security")
        require(
            (security is None and review_schema is None)
            or (review_schema is not None and _valid_review_security(security)),
            f"{label} review owner/ACL/trigger/RLS evidence missing or unsafe",
        )
        access = report.get("release_review_access")
        require(
            (access is None and review_schema is None)
            or (review_schema is not None and _valid_review_access(access)),
            f"{label} review schema/column privilege evidence missing or unsafe",
        )
        reviews = report.get("release_reviews")
        events = report.get("release_review_events")
        require((reviews is None) == (events is None),
                f"{label} review table pair is inconsistent")
        require((reviews is None) == (review_schema is None),
                f"{label} review structure/row evidence presence mismatch")
        expected_null = report.get("youtube_all_null_selection")
        require(_valid_digest(expected_null),
                f"{label} expected all-NULL selection fingerprint unavailable")
        common = report.get("youtube_manifest_common")
        require(_valid_digest(common), f"{label} YouTube manifest evidence missing")
        require(_valid_manifest_schema(report.get("youtube_manifest_schema")),
                f"{label} manifest common column/primary-key evidence missing or invalid")
        require(_valid_manifest_integrity(report.get("youtube_manifest_integrity")),
                f"{label} manifest index/constraint evidence missing or unsafe")
        require(_valid_manifest_security(report.get("youtube_manifest_security")),
                f"{label} manifest ownership/grant/trigger/RLS evidence missing or unsafe")
        require(_valid_manifest_storage(report.get("youtube_manifest_storage")),
                f"{label} manifest persistence/replication/rewrite evidence missing or unsafe")
        require(
            _valid_digest(expected_null) and _valid_digest(common)
            and expected_null["count"] == common["count"],
            f"{label} expected all-NULL fingerprint row count differs",
        )
        column_contract = report.get("youtube_selection_column_contract")
        has_selection = report.get("youtube_selection_column_present")
        require(type(has_selection) is bool,
                f"{label} selection-column presence is unknown")
        selection = report.get("youtube_selection")
        count = report.get("youtube_selection_nonnull")
        if has_selection is True:
            require(_valid_selection_contract(column_contract),
                    f"{label} selection column type/nullability differs from JSON NULL")
            require(_valid_digest(selection),
                    f"{label} present selection column lacks a valid digest")
            require(
                _valid_digest(selection) and _valid_digest(common)
                and selection["count"] == common["count"],
                f"{label} selection row count differs from manifest count",
            )
            require(
                type(count) is int and _valid_digest(selection)
                and 0 <= count <= selection["count"],
                f"{label} selection non-null count is missing or out of range",
            )
        elif has_selection is False:
            require(column_contract is None,
                    f"{label} absent selection column has unexpected schema evidence")
            require(selection is None and count is None,
                    f"{label} absent selection column has unexpected evidence")

    for field in (*_FINGERPRINTS, "youtube_all_null_selection", "release_review_schema",
                  "release_review_security", "release_review_access",
                  "youtube_manifest_schema", "youtube_manifest_integrity",
                  "youtube_manifest_security", "youtube_manifest_storage",
                  "release_review_storage"):
        require(source.get(field) == before.get(field),
                f"clone differs from source before migration: {field}")
    require(
        source.get("youtube_selection_column_present")
        == before.get("youtube_selection_column_present"),
        "clone selection-column presence differs from source",
    )
    require(source.get("youtube_selection_nonnull") ==
            before.get("youtube_selection_nonnull"),
            "clone selection non-null count differs from source")

    common = before.get("youtube_manifest_common")
    require(_valid_digest(common), "YouTube common-column evidence unavailable")
    require(after.get("youtube_manifest_common") == common,
            "YouTube manifest common columns changed during migration")
    require(after.get("youtube_manifest_schema") == before.get("youtube_manifest_schema"),
            "YouTube manifest common column structure changed during migration")
    require(after.get("youtube_manifest_integrity") == before.get("youtube_manifest_integrity"),
            "YouTube manifest indexes or constraints changed during migration")
    require(after.get("youtube_manifest_security") == before.get("youtube_manifest_security"),
            "YouTube manifest ownership, grants or security changed during migration")
    require(after.get("youtube_manifest_storage") == before.get("youtube_manifest_storage"),
            "YouTube manifest durability/replication/rules changed during migration")
    require(after.get("youtube_all_null_selection") == before.get("youtube_all_null_selection"),
            "expected all-NULL selection fingerprint changed during migration")
    require(after.get("youtube_selection_column_present") is True,
            "merged clone lacks the YouTube selection column")
    require(_valid_selection_contract(after.get("youtube_selection_column_contract")),
            "merged clone has wrong selection column schema")

    if original_rev == RELEASE:
        require(before.get("youtube_selection_column_present") is False,
                "review-first branch already has unexpected selection column")
        require(before.get("youtube_selection") is None and
                before.get("youtube_selection_nonnull") is None,
                "review-first branch contains unexpected selection evidence")
        for field in ("release_reviews", "release_review_events"):
            require(_valid_digest(before.get(field)),
                    f"review-first branch missing {field}")
            require(after.get(field) == before.get(field),
                    f"{field} was changed by migration")
        require(after.get("release_review_schema") == before.get("release_review_schema"),
                "review schema changed during YouTube sibling upgrade")
        require(after.get("release_review_storage") == before.get("release_review_storage"),
                "review table storage changed during YouTube sibling upgrade")
        require(after.get("release_review_security") == before.get("release_review_security"),
                "review table ownership or security changed during YouTube sibling upgrade")
        require(after.get("release_review_access") == before.get("release_review_access"),
                "review schema or column grants changed during YouTube sibling upgrade")
        require(after.get("youtube_selection_nonnull") == 0,
                "selection column contains unexpected data after addition")
        require(after.get("youtube_selection") == before.get("youtube_all_null_selection"),
                "new selection column does not match the expected all-NULL digest")
        require(_valid_digest(after.get("youtube_selection")) and
                after["youtube_selection"]["count"] ==
                (common["count"] if isinstance(common, Mapping) else None),
                "added selection column fingerprint is missing or incomplete")
    elif original_rev == YOUTUBE:
        require(before.get("release_reviews") is None and
                before.get("release_review_events") is None,
                "YouTube-first branch already has review tables")
        require(before.get("youtube_selection_column_present") is True,
                "YouTube-first branch lacks selection column")
        require(_valid_digest(before.get("youtube_selection")),
                "YouTube-first selection-column fingerprint unavailable")
        require(before.get("youtube_selection_nonnull") is not None,
                "YouTube-first non-null selection count unavailable")
        require(after.get("youtube_selection") == before.get("youtube_selection") and
                after.get("youtube_selection_nonnull") ==
                before.get("youtube_selection_nonnull"),
                "existing YouTube selection data changed")
        require(_valid_review_schema(after.get("release_review_schema")),
                "review schema was not created as expected")
        require(after.get("release_reviews") == _EMPTY and
                after.get("release_review_events") == _EMPTY,
                "new review tables are not empty")

    return {"passed": not failures, "failures": failures}


def _unique_json_fields(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate snapshot field")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("nonfinite snapshot value")


def _read_bounded_snapshot(path: str) -> dict[str, Any]:
    with Path(path).open("rb") as stream:
        content = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds bounded input")
    value = json.loads(
        content.decode("utf-8-sig"),
        object_pairs_hook=_unique_json_fields,
        parse_constant=_reject_nonfinite,
    )
    if not isinstance(value, dict):
        raise TypeError("snapshot must be a JSON object")
    return value


def main(arguments: list[str] | None = None) -> int:
    """Compare three saved digest-only files; emit only pass/fail reasons."""
    paths = sys.argv[1:] if arguments is None else arguments
    if len(paths) != 3:
        print(json.dumps({"passed": False, "failures": [
            "provide source.json, clone_before.json, clone_after.json"
        ]}))
        return 2
    try:
        snapshots = [_read_bounded_snapshot(path) for path in paths]
        verdict = compare_preservation(*snapshots)
    except (OSError, ValueError, TypeError):
        verdict = {"passed": False, "failures": ["cannot read valid digest-only snapshots"]}
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

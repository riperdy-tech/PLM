"""Read-only fingerprint of release-review records for 0038 branch reconciliation.

Use on a source DB and an approved isolated clone. Only counts and SHA256
digests are emitted; record contents and credentials are never printed.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("unsupported database value type in review snapshot")


def table_fingerprint(
    rows: Iterable[Mapping[str, Any]], *, id_column: str,
    max_rows: int = 10000,
) -> dict[str, str | int]:
    """Digest all columns per row in stable PK order; reject partial/duplicate input."""
    entries: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for row in rows:
        identifier = row.get(id_column)
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("missing or duplicate release-review identity")
        seen.add(identifier)
        if len(seen) > max_rows:
            raise ValueError("review snapshot row limit exceeded; no partial digest")
        serializable = {str(k): _json_safe(v) for k, v in row.items()}
        payload = json.dumps(
            serializable, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        entries.append((identifier, payload))

    digest = hashlib.sha256()
    for _key, payload in sorted(entries, key=lambda item: item[0]):
        # Length framing prevents any concatenation ambiguity.
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return {"count": len(entries), "sha256": digest.hexdigest()}


def selection_column_contract(
    columns: Iterable[Mapping[str, Any]],
) -> dict[str, str | bool | None] | None:
    """Capture selection column type, default and generation/identity metadata."""
    matching = [col for col in columns if col.get("name") == "selection_json"]
    if not matching:
        return None
    if len(matching) != 1:
        raise ValueError("selection column schema evidence unavailable")
    column = matching[0]
    default = column.get("default")
    if (
        "type" not in column or type(column.get("nullable")) is not bool
        or (default is not None and type(default) is not str)
    ):
        raise ValueError("selection column schema evidence unavailable")
    return {
        "type": str(column["type"]).upper(),
        "nullable": column["nullable"],
        "server_default": default,
        "has_computed": column.get("computed") is not None,
        "has_identity": column.get("identity") is not None,
    }


def manifest_common_schema_contract(
    columns: Iterable[Mapping[str, Any]], primary_key: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture pre-0038 manifest column structure, excluding selection_json."""
    details: list[dict[str, Any]] = []
    names: set[str] = set()
    for column in columns:
        name = column.get("name")
        if name == "selection_json":
            continue
        type_value = column.get("type")
        type_name = str(type_value).upper() if type_value is not None else ""
        default = column.get("default")
        if (
            type(name) is not str or not name or name in names
            or not type_name or type(column.get("nullable")) is not bool
            or (default is not None and type(default) is not str)
        ):
            raise ValueError("manifest common column evidence unavailable")
        names.add(name)
        timestamp = type_name.startswith("TIMESTAMP")
        timezone = getattr(type_value, "timezone", None) if timestamp else None
        if timestamp and type(timezone) is not bool:
            raise ValueError("manifest timezone evidence unavailable")
        details.append({
            "name": name, "type": type_name, "nullable": column["nullable"],
            "server_default": default, "timezone": timezone,
            "has_computed": column.get("computed") is not None,
            "has_identity": column.get("identity") is not None,
        })
    pk = primary_key.get("constrained_columns")
    if names == set() or "manifest_id" not in names or pk != ["manifest_id"]:
        raise ValueError("manifest common primary key evidence unavailable")
    return {"columns": sorted(details, key=lambda item: item["name"]),
            "primary_key": ["manifest_id"]}


_EXPECTED_MANIFEST_INDEXES = frozenset({
    "publish_manifests_pkey",
    "ix_youtube_publish_manifests_channel_id",
    "ix_youtube_publish_manifests_experiment_id",
    "ix_youtube_publish_manifests_idempotency_key",
    "ix_youtube_publish_manifests_media_package_id",
    "ix_youtube_publish_manifests_release_manifest_id",
})


def manifest_index_definitions(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, str | bool]]:
    """Fail closed on missing, extra, invalid or duplicated manifest index DDL."""
    result: dict[str, dict[str, str | bool]] = {}
    for row in rows:
        name = row.get("index_name")
        definition = row.get("definition")
        if (
            type(name) is not str or name not in _EXPECTED_MANIFEST_INDEXES
            or name in result or type(definition) is not str or not definition
            or type(row.get("is_primary")) is not bool
            or row.get("is_valid") is not True
            or row.get("is_ready") is not True
            or row.get("is_live") is not True
        ):
            raise ValueError("manifest index definition evidence missing or invalid")
        result[name] = {
            "definition": definition,
            "is_primary": row["is_primary"],
        }
    if set(result) != _EXPECTED_MANIFEST_INDEXES:
        raise ValueError("manifest index definition evidence incomplete")
    if sum(item["is_primary"] is True for item in result.values()) != 1 or (
        result["publish_manifests_pkey"]["is_primary"] is not True
    ):
        raise ValueError("manifest primary index evidence invalid")
    return dict(sorted(result.items()))


def manifest_integrity_contract(
    inspector: Any, *, index_definitions: Mapping[str, Mapping[str, str | bool]],
) -> dict[str, Any]:
    """Read indexes and integrity constraints on the existing manifest table."""
    reflected = inspector.get_indexes("publish_manifests", schema="youtube")
    if (
        set(index_definitions) != _EXPECTED_MANIFEST_INDEXES
        or {idx["name"] for idx in reflected}
        != _EXPECTED_MANIFEST_INDEXES - {"publish_manifests_pkey"}
    ):
        raise ValueError("manifest index catalog and reflection differ")
    return {
        "index_definitions": dict(sorted(index_definitions.items())),
        "check_constraints": sorted([
            {"name": item.get("name"), "sqltext": item.get("sqltext")}
            for item in inspector.get_check_constraints("publish_manifests", schema="youtube")
        ], key=lambda item: (str(item["name"]), str(item["sqltext"]))),
        "foreign_keys": sorted([
            {
                "name": item.get("name"),
                "columns": item.get("constrained_columns"),
                "referred_schema": item.get("referred_schema"),
                "referred_table": item.get("referred_table"),
                "referred_columns": item.get("referred_columns"),
            }
            for item in inspector.get_foreign_keys("publish_manifests", schema="youtube")
        ], key=lambda item: str(item["name"])),
        "unique_constraints": sorted([
            {"name": item.get("name"), "columns": item.get("column_names")}
            for item in inspector.get_unique_constraints("publish_manifests", schema="youtube")
        ], key=lambda item: str(item["name"])),
    }


def manifest_security_contract(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Attest existing manifest ownership, privileges and execution controls.

    ACL and owner strings are hashed before returning evidence. Counts alone
    cannot prove preservation when a grant changes without changing its count.
    """
    found = list(rows)
    if len(found) != 1:
        raise ValueError("manifest security catalog row missing or duplicated")
    row = found[0]
    expected = {
        "table_name", "owner_name", "schema_owner_name",
        "table_acl", "schema_acl", "column_acl", "schema_default_acl",
        "rls_enabled", "rls_forced", "custom_triggers", "policies",
    }
    if set(row) != expected or row["table_name"] != "publish_manifests":
        raise ValueError("manifest security catalog identity unavailable")
    for field in ("owner_name", "schema_owner_name"):
        if type(row[field]) is not str or not row[field]:
            raise ValueError("manifest security ownership unavailable")
    for field in ("table_acl", "schema_acl", "column_acl", "schema_default_acl"):
        if row[field] is not None and (type(row[field]) is not str or not row[field]):
            raise ValueError("manifest security ACL unavailable")
    for field in ("rls_enabled", "rls_forced"):
        if type(row[field]) is not bool:
            raise ValueError("manifest security RLS evidence unavailable")
    for field in ("custom_triggers", "policies"):
        if type(row[field]) is not int or row[field] < 0:
            raise ValueError("manifest security object count unavailable")

    def digest(value: str | None) -> str | None:
        return hashlib.sha256(value.encode("utf-8")).hexdigest() if value is not None else None

    return {
        "owner_sha256": digest(row["owner_name"]),
        "schema_owner_sha256": digest(row["schema_owner_name"]),
        "table_acl_sha256": digest(row["table_acl"]),
        "schema_acl_sha256": digest(row["schema_acl"]),
        "column_acl_sha256": digest(row["column_acl"]),
        "schema_default_acl_sha256": digest(row["schema_default_acl"]),
        "rls_enabled": row["rls_enabled"],
        "rls_forced": row["rls_forced"],
        "custom_triggers": row["custom_triggers"],
        "policies": row["policies"],
    }


def manifest_storage_contract(
    rows: Iterable[Mapping[str, Any]], *, expected_table: str = "publish_manifests",
) -> dict[str, Any]:
    """Capture relation durability and rewrite semantics independently of row hashes."""
    records = list(rows)
    if len(records) != 1:
        raise ValueError("manifest storage catalog row missing or duplicated")
    record = records[0]
    fields = {
        "table_name", "relation_kind", "persistence", "replica_identity",
        "is_partition", "rewrite_rules",
    }
    if (
        set(record) != fields or record["table_name"] != expected_table
        or any(
            type(record[field]) is not str or len(record[field]) != 1
            for field in ("relation_kind", "persistence", "replica_identity")
        )
        or type(record["is_partition"]) is not bool
        or type(record["rewrite_rules"]) is not int
        or record["rewrite_rules"] < 0
    ):
        raise ValueError("manifest storage catalog evidence unavailable")
    return {key: record[key] for key in sorted(fields - {"table_name"})}


def review_storage_contract(
    rows: Iterable[Mapping[str, Any]], *, has_reviews: bool,
) -> dict[str, dict[str, Any]] | None:
    """Require catalog evidence for both approval-history tables, or neither."""
    found = list(rows)
    if not has_reviews:
        if found:
            raise ValueError("unexpected review storage evidence without review tables")
        return None
    expected = {"release_reviews", "release_review_events"}
    names = [row.get("table_name") for row in found]
    if len(found) != 2 or set(names) != expected:
        raise ValueError("review storage table pair missing or duplicated")
    return {
        name: manifest_storage_contract(
            (row for row in found if row["table_name"] == name),
            expected_table=name,
        )
        for name in sorted(expected)
    }


def review_index_definitions(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Preserve full PostgreSQL non-PK index DDL; reject missing catalog facts."""
    result: dict[str, dict[str, str]] = {
        "release_reviews": {}, "release_review_events": {},
    }
    for row in rows:
        table = row.get("table_name")
        index = row.get("index_name")
        ddl = row.get("definition")
        if (
            type(table) is not str or table not in result
            or type(index) is not str or not index
            or type(ddl) is not str or not ddl
            or row.get("is_valid") is not True
            or row.get("is_ready") is not True
            or row.get("is_live") is not True
            or index in result[table]
        ):
            raise ValueError("unavailable or duplicated PostgreSQL index definition")
        result[table][index] = ddl
    return result


def review_security_contract(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, str | bool | int]] | None:
    """Read table-level security facts without exposing raw PostgreSQL role names."""
    tables = {"release_reviews", "release_review_events"}
    result: dict[str, dict[str, str | bool | int]] = {}
    for row in rows:
        name = row.get("table_name")
        owner = row.get("owner_name")
        if type(name) is not str or name not in tables or name in result:
            raise ValueError("invalid or duplicate review security table identity")
        if type(owner) is not str or not owner:
            raise ValueError("review table owner is unavailable")
        for field in ("default_acl", "rls_enabled", "rls_forced"):
            if type(row.get(field)) is not bool:
                raise ValueError("review security boolean evidence unavailable")
        for field in ("custom_triggers", "policies"):
            if type(row.get(field)) is not int or row[field] < 0:
                raise ValueError("review security count evidence unavailable")
        result[name] = {
            "owner_sha256": hashlib.sha256(owner.encode("utf-8")).hexdigest(),
            "default_acl": row["default_acl"],
            "rls_enabled": row["rls_enabled"],
            "rls_forced": row["rls_forced"],
            "custom_triggers": row["custom_triggers"],
            "policies": row["policies"],
        }
    if not result:
        return None
    if set(result) != tables:
        raise ValueError("incomplete review security table pair")
    return result


def review_access_contract(
    rows: Iterable[Mapping[str, Any]], *, has_reviews: bool,
) -> dict[str, bool | int] | None:
    """Collect schema/column ACL facts for the historical review-table pair."""
    found = list(rows)
    if not has_reviews:
        if found:
            raise ValueError("unexpected review access evidence without tables")
        return None
    if len(found) != 1 or found[0].get("schema_name") != "ops":
        raise ValueError("review schema ACL evidence missing or duplicated")
    row = found[0]
    if (
        set(row) != {
            "schema_name", "schema_acl_default", "schema_default_acl_entries",
            "column_acl_entries", "review_table_count",
        }
        or type(row["schema_acl_default"]) is not bool
        or any(
            type(row[field]) is not int or row[field] < 0
            for field in (
                "schema_default_acl_entries", "column_acl_entries",
                "review_table_count",
            )
        )
        or row["review_table_count"] != 2
    ):
        raise ValueError("review schema/column privileges unavailable")
    return {
        "schema_acl_default": row["schema_acl_default"],
        "schema_default_acl_entries": row["schema_default_acl_entries"],
        "column_acl_entries": row["column_acl_entries"],
    }


def review_schema_contract(
    inspector: Any, *, index_definitions: Mapping[str, Mapping[str, str]],
) -> dict[str, Any] | None:
    """Record review columns, defaults, keys, checks and exact index DDL."""
    tables = ("release_reviews", "release_review_events")
    if set(index_definitions) != set(tables):
        raise ValueError("review index catalog evidence incomplete")
    exists = [inspector.has_table(name, schema="ops") for name in tables]
    if not any(exists):
        return None
    if not all(exists):
        raise ValueError("incomplete release-review table schema")
    result: dict[str, Any] = {}
    for name in tables:
        columns: list[dict[str, Any]] = []
        for col in inspector.get_columns(name, schema="ops"):
            type_name = str(col["type"]).upper()
            if type(col.get("nullable")) is not bool:
                raise ValueError("review column nullability is unknown")
            server_default = col.get("default")
            if server_default is not None and not isinstance(server_default, str):
                raise ValueError("review column server default is not inspectable")
            detail: dict[str, Any] = {
                "name": col["name"], "type": type_name,
                "nullable": col["nullable"], "server_default": server_default,
            }
            if type_name.startswith("TIMESTAMP"):
                detail["timezone"] = getattr(col["type"], "timezone", None)
            columns.append(detail)
        primary = inspector.get_pk_constraint(name, schema="ops")
        indexes = inspector.get_indexes(name, schema="ops")
        table_ddls = index_definitions[name]
        if type(table_ddls) is not dict or set(table_ddls) != {
            idx["name"] for idx in indexes
        }:
            raise ValueError("index catalog differs from reflected indexes")
        result[name] = {
            "index_definitions": dict(sorted(table_ddls.items())),
            "columns": sorted(columns, key=lambda c: c["name"]),
            "primary_key": sorted(primary.get("constrained_columns") or []),
            "indexes": sorted(
                [
                    {
                        "name": idx["name"],
                        "columns": idx["column_names"],
                        "unique": idx["unique"],
                    }
                    for idx in indexes
                ],
                key=lambda idx: idx["name"],
            ),
            "check_constraints": sorted(
                [
                    {"name": item.get("name"), "sqltext": item.get("sqltext")}
                    for item in inspector.get_check_constraints(name, schema="ops")
                ],
                key=lambda item: (str(item["name"]), str(item["sqltext"])),
            ),
            "foreign_keys": sorted(
                [
                    {
                        "name": item.get("name"),
                        "columns": item.get("constrained_columns"),
                        "referred_schema": item.get("referred_schema"),
                        "referred_table": item.get("referred_table"),
                        "referred_columns": item.get("referred_columns"),
                    }
                    for item in inspector.get_foreign_keys(name, schema="ops")
                ],
                key=lambda item: str(item["name"]),
            ),
            "unique_constraints": sorted(
                [
                    {"name": item.get("name"), "columns": item.get("column_names")}
                    for item in inspector.get_unique_constraints(name, schema="ops")
                ],
                key=lambda item: str(item["name"]),
            ),
        }
    return result


def read_only_review_snapshot(*, settings: Any = None) -> dict[str, Any]:
    """Read both 0038 branches in one REPEATABLE READ, READ ONLY DB snapshot."""
    import sqlalchemy as sa
    from plm_platform.db import build_engine

    # A guarded operator preflight passes the same resolved configuration
    # used for its pre-connection database-name check.
    engine = build_engine(settings) if settings is not None else build_engine()
    try:
        with engine.connect() as connection:
            connection.execute(sa.text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            ))
            try:
                identity = connection.execute(
                    sa.text("SELECT current_database()")
                ).scalar_one()
                revision = connection.execute(
                    sa.text("SELECT version_num FROM public.alembic_version")
                ).scalar_one()
                inspector = sa.inspect(connection)
                has_reviews = inspector.has_table("release_reviews", schema="ops")
                has_events = inspector.has_table("release_review_events", schema="ops")
                if has_reviews != has_events:
                    raise ValueError("one release-review table is missing")
                # Capture full non-primary-key DDL from pg_catalog in the same
                # read-only snapshot. Reflection alone omits index predicates,
                # expressions, opclasses, sort order, and access methods.
                catalog_indexes = connection.execute(sa.text("""
                    SELECT t.relname AS table_name, i.relname AS index_name,
                           pg_catalog.pg_get_indexdef(i.oid) AS definition,
                           x.indisvalid AS is_valid, x.indisready AS is_ready,
                           x.indislive AS is_live
                    FROM pg_catalog.pg_index AS x
                    JOIN pg_catalog.pg_class AS i ON i.oid = x.indexrelid
                    JOIN pg_catalog.pg_class AS t ON t.oid = x.indrelid
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = t.relnamespace
                    WHERE n.nspname = 'ops'
                      AND t.relname IN ('release_reviews', 'release_review_events')
                      AND NOT x.indisprimary
                    ORDER BY t.relname, i.relname
                """)).mappings()
                review_structure = review_schema_contract(
                    inspector,
                    index_definitions=review_index_definitions(catalog_indexes),
                )
                security_rows = connection.execute(sa.text("""
                    SELECT c.relname AS table_name,
                           pg_catalog.pg_get_userbyid(c.relowner) AS owner_name,
                           (c.relacl IS NULL) AS default_acl,
                           c.relrowsecurity AS rls_enabled,
                           c.relforcerowsecurity AS rls_forced,
                           (SELECT count(*) FROM pg_catalog.pg_trigger t
                            WHERE t.tgrelid = c.oid AND NOT t.tgisinternal)
                            AS custom_triggers,
                           (SELECT count(*) FROM pg_catalog.pg_policy p
                            WHERE p.polrelid = c.oid) AS policies
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'ops'
                      AND c.relname IN ('release_reviews', 'release_review_events')
                      AND c.relkind IN ('r', 'p')
                    ORDER BY c.relname
                """)).mappings()
                review_security = review_security_contract(security_rows)
                if (review_security is None) != (review_structure is None):
                    raise ValueError("review table security evidence missing")
                review_storage = review_storage_contract(
                    connection.execute(sa.text("""
                        SELECT c.relname AS table_name,
                               c.relkind AS relation_kind,
                               c.relpersistence AS persistence,
                               c.relreplident AS replica_identity,
                               c.relispartition AS is_partition,
                               (SELECT count(*) FROM pg_catalog.pg_rewrite r
                                WHERE r.ev_class = c.oid) AS rewrite_rules
                        FROM pg_catalog.pg_class c
                        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'ops'
                          AND c.relname IN ('release_reviews', 'release_review_events')
                        ORDER BY c.relname
                    """)).mappings(),
                    has_reviews=has_reviews,
                )
                access_rows = (
                    connection.execute(sa.text("""
                        SELECT n.nspname AS schema_name,
                               (n.nspacl IS NULL) AS schema_acl_default,
                               (SELECT count(*) FROM pg_catalog.pg_default_acl d
                                WHERE d.defaclnamespace = n.oid
                                  AND d.defaclobjtype = 'r')
                                AS schema_default_acl_entries,
                               (SELECT count(*) FROM pg_catalog.pg_attribute a
                                JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
                                WHERE c.relnamespace = n.oid
                                  AND c.relname IN (
                                      'release_reviews', 'release_review_events'
                                  )
                                  AND a.attnum > 0 AND NOT a.attisdropped
                                  AND a.attacl IS NOT NULL) AS column_acl_entries,
                               (SELECT count(*) FROM pg_catalog.pg_class c
                                WHERE c.relnamespace = n.oid
                                  AND c.relname IN (
                                      'release_reviews', 'release_review_events'
                                  )
                                  AND c.relkind IN ('r', 'p')) AS review_table_count
                        FROM pg_catalog.pg_namespace n
                        WHERE n.nspname = 'ops'
                    """)).mappings()
                    if has_reviews else []
                )
                review_access = review_access_contract(
                    access_rows, has_reviews=has_reviews,
                )
                review_signature = None
                event_signature = None
                if has_reviews:
                    reviews = connection.execute(sa.text(
                        "SELECT * FROM ops.release_reviews ORDER BY package_id LIMIT 10001"
                    )).mappings()
                    review_signature = table_fingerprint(reviews, id_column="package_id")
                    events = connection.execute(sa.text(
                        "SELECT * FROM ops.release_review_events ORDER BY event_id LIMIT 10001"
                    )).mappings()
                    event_signature = table_fingerprint(events, id_column="event_id")

                # This table predates either 0038 sibling; its common columns
                # must remain byte-for-byte equivalent across the merge.
                if not inspector.has_table("publish_manifests", schema="youtube"):
                    raise ValueError("required YouTube publish manifest table is missing")
                catalog_manifest_indexes = connection.execute(sa.text("""
                    SELECT i.relname AS index_name,
                           pg_catalog.pg_get_indexdef(i.oid) AS definition,
                           x.indisprimary AS is_primary,
                           x.indisvalid AS is_valid, x.indisready AS is_ready,
                           x.indislive AS is_live
                    FROM pg_catalog.pg_index x
                    JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid
                    JOIN pg_catalog.pg_class t ON t.oid = x.indrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = 'youtube'
                      AND t.relname = 'publish_manifests'
                    ORDER BY i.relname
                """)).mappings()
                manifest_integrity = manifest_integrity_contract(
                    inspector,
                    index_definitions=manifest_index_definitions(catalog_manifest_indexes),
                )
                manifest_security = manifest_security_contract(
                    connection.execute(sa.text("""
                        SELECT c.relname AS table_name,
                               pg_catalog.pg_get_userbyid(c.relowner) AS owner_name,
                               pg_catalog.pg_get_userbyid(n.nspowner) AS schema_owner_name,
                               c.relacl::text AS table_acl,
                               n.nspacl::text AS schema_acl,
                               (SELECT jsonb_agg(
                                    jsonb_build_array(a.attnum, a.attacl::text)
                                    ORDER BY a.attnum)::text
                                FROM pg_catalog.pg_attribute a
                                WHERE a.attrelid = c.oid AND a.attnum > 0
                                  AND NOT a.attisdropped AND a.attacl IS NOT NULL)
                                AS column_acl,
                               (SELECT jsonb_agg(
                                    jsonb_build_array(d.defaclrole, d.defaclobjtype,
                                                      d.defaclacl::text)
                                    ORDER BY d.defaclrole, d.defaclobjtype)::text
                                FROM pg_catalog.pg_default_acl d
                                WHERE d.defaclnamespace = n.oid)
                                AS schema_default_acl,
                               c.relrowsecurity AS rls_enabled,
                               c.relforcerowsecurity AS rls_forced,
                               (SELECT count(*) FROM pg_catalog.pg_trigger t
                                WHERE t.tgrelid = c.oid AND NOT t.tgisinternal)
                                AS custom_triggers,
                               (SELECT count(*) FROM pg_catalog.pg_policy p
                                WHERE p.polrelid = c.oid) AS policies
                        FROM pg_catalog.pg_class c
                        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'youtube'
                          AND c.relname = 'publish_manifests'
                          AND c.relkind IN ('r', 'p')
                    """)).mappings()
                )
                manifest_storage = manifest_storage_contract(
                    connection.execute(sa.text("""
                        SELECT c.relname AS table_name,
                               c.relkind AS relation_kind,
                               c.relpersistence AS persistence,
                               c.relreplident AS replica_identity,
                               c.relispartition AS is_partition,
                               (SELECT count(*) FROM pg_catalog.pg_rewrite r
                                WHERE r.ev_class = c.oid) AS rewrite_rules
                        FROM pg_catalog.pg_class c
                        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'youtube'
                          AND c.relname = 'publish_manifests'
                    """)).mappings()
                )
                manifest_rows = [
                    dict(row) for row in connection.execute(sa.text(
                        "SELECT * FROM youtube.publish_manifests ORDER BY manifest_id LIMIT 10001"
                    )).mappings()
                ]
                manifest_columns = inspector.get_columns(
                    "publish_manifests", schema="youtube",
                )
                selection_contract = selection_column_contract(manifest_columns)
                manifest_schema = manifest_common_schema_contract(
                    manifest_columns,
                    inspector.get_pk_constraint("publish_manifests", schema="youtube"),
                )
                has_selection = selection_contract is not None
                common = table_fingerprint(
                    [
                        {k: v for k, v in row.items() if k != "selection_json"}
                        for row in manifest_rows
                    ],
                    id_column="manifest_id",
                )
                # Independently computed expected digest if every row were NULL.
                # This is available even before the selection_json column exists.
                # A reviewer can then verify that a new nullable column was
                # added without silently populating or replacing its values.
                expected_null_selection = table_fingerprint(
                    [
                        {"manifest_id": row["manifest_id"], "selection_json": None}
                        for row in manifest_rows
                    ],
                    id_column="manifest_id",
                )
                selection = None
                selection_nonnull = None
                if has_selection:
                    selection = table_fingerprint(
                        [
                            {"manifest_id": row["manifest_id"],
                             "selection_json": row["selection_json"]}
                            for row in manifest_rows
                        ],
                        id_column="manifest_id",
                    )
                    selection_nonnull = sum(
                        row["selection_json"] is not None for row in manifest_rows
                    )
                return {
                    "database": identity,
                    "alembic_revision": revision,
                    "release_reviews": review_signature,
                    "release_review_events": event_signature,
                    "release_review_schema": review_structure,
                    "release_review_security": review_security,
                    "release_review_access": review_access,
                    "release_review_storage": review_storage,
                    "youtube_manifest_common": common,
                    "youtube_manifest_schema": manifest_schema,
                    "youtube_manifest_integrity": manifest_integrity,
                    "youtube_manifest_security": manifest_security,
                    "youtube_manifest_storage": manifest_storage,
                    "youtube_selection_column_present": has_selection,
                    "youtube_selection_column_contract": selection_contract,
                    "youtube_selection": selection,
                    "youtube_all_null_selection": expected_null_selection,
                    "youtube_selection_nonnull": selection_nonnull,
                    "read_only": True,
                    "transaction_isolation": "repeatable_read",
                }
            finally:
                connection.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    print(json.dumps(read_only_review_snapshot(), sort_keys=True))

"""Isolated-only PostgreSQL rehearsal of the B03 P4 0040 cost migration.

Does not modify, migrate or stamp the connected development DB. Requires
explicit opt-in. Original rows are compared by typed read-only fingerprints,
and only an independently provisioned disposable Docker PostgreSQL is upgraded.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from alembic.config import Config
from plm_platform.config import Env, Settings, load_settings
from plm_platform.db import build_engine, database_url
from sqlalchemy import text

from tests.support.migration_isolated_rehearsal import (
    ROOT,
    SOURCE_CONTAINER,
    _create_database,
    _dump,
    _inspect,
    _new_cluster,
    _restore,
    _target_settings,
    _upgrade,
)
from tests.support.migration_review_snapshot import read_only_review_snapshot

SOURCE_REV = "0039_merge_0038_branches"
TARGET_REV = "0040_p4_unpriced_costs"
OPT_IN = "--approved-isolated-test-only"
TABLES = ("generation_attempts", "provider_cost_records")
NEW_COLUMNS = {
    "generation_attempts": {"money_basis", "cost_currency", "cost_receipt_ref"},
    "provider_cost_records": {"money_basis", "cost_receipt_ref"},
}


def _source_settings() -> Settings:
    settings = load_settings(dotenv_path=ROOT / ".env")
    url = database_url(settings)
    if (
        settings.env != Env.LOCAL or url.database != "plm"
        or url.host not in ("127.0.0.1", "localhost") or url.port != 5432
        or not url.username or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", url.username)
    ):
        raise RuntimeError("isolated rehearsal source must be known local PLM development DB")
    info = _inspect(SOURCE_CONTAINER)
    bound = info["HostConfig"]["PortBindings"]["5432/tcp"]
    if (
        info["State"]["Running"] is not True
        or not info["Config"]["Image"].startswith("postgres:16")
        or len(bound) != 1 or bound[0]["HostIp"] != "127.0.0.1"
        or bound[0]["HostPort"] != "5432"
    ):
        raise RuntimeError("connected local PostgreSQL container identity changed")
    return settings


def _observe(
    settings: Settings, *,
    expected_revision: str, original_columns: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            try:
                identity = connection.execute(text("SELECT current_database()")).scalar_one()
                version = connection.execute(text(
                    "SELECT version_num FROM public.alembic_version"
                )).scalars().all()
                if identity != database_url(settings).database or version != [expected_revision]:
                    raise RuntimeError("database identity or migration version is unexpected")
                fingerprints: dict[str, Any] = {}
                for table in TABLES:
                    columns = [row[0] for row in connection.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='music' AND table_name=:tbl "
                        "ORDER BY ordinal_position"
                    ), {"tbl": table})]
                    if not columns or any(
                        re.fullmatch(r"[a-z_][a-z0-9_]*", col) is None for col in columns
                    ):
                        raise RuntimeError("P4 table shape unavailable or unsafe")
                    if original_columns is None:
                        if NEW_COLUMNS[table] & set(columns):
                            raise RuntimeError("source already contains 0040 cost schema")
                        selected = columns
                    else:
                        selected = original_columns[table]
                        if not set(selected) <= set(columns):
                            raise RuntimeError("0040 dropped a historical cost column")
                        if not NEW_COLUMNS[table] <= set(columns):
                            raise RuntimeError("0040 missing a required provenance field")
                        specs = connection.execute(text(
                            "SELECT column_name, is_nullable, column_default "
                            "FROM information_schema.columns "
                            "WHERE table_schema='music' AND table_name=:tbl"
                        ), {"tbl": table}).mappings().all()
                        info = {row["column_name"]: row for row in specs}
                        for col in NEW_COLUMNS[table]:
                            if info[col]["column_default"] is not None:
                                raise RuntimeError("provenance column retains implicit default")
                        if any(info[name]["is_nullable"] != "YES" for name in (
                            ("actual_cost", "cost_estimate") if table == "generation_attempts"
                            else ("actual_amount", "estimated_amount", "currency")
                        )):
                            raise RuntimeError("0040 unknown monetary columns not nullable")
                        expected_count = connection.execute(text(
                            f"SELECT COUNT(*) FROM music.{table} "
                            "WHERE money_basis='legacy_unverified' AND cost_receipt_ref IS NULL"
                        )).scalar_one()
                    projection = ", ".join(f'"{col}"' for col in selected)
                    rows = connection.execute(text(
                        f"SELECT {projection} FROM music.{table} ORDER BY id LIMIT 100001"
                    )).mappings().all()
                    if len(rows) > 100000:
                        raise RuntimeError("source cost table exceeds bounded rehearsal")
                    digest = hashlib.sha256()
                    for row in rows:
                        line = json.dumps(dict(row), sort_keys=True, default=str,
                                          separators=(",", ":"), allow_nan=False)
                        digest.update(line.encode("utf-8") + b"\n")
                    if original_columns is not None and expected_count != len(rows):
                        raise RuntimeError("existing cost records are not marked legacy_unverified")
                    fingerprints[table] = {
                        "columns": selected,
                        "count": len(rows),
                        "sha256": digest.hexdigest(),
                    }
                return fingerprints
            finally:
                connection.rollback()
    finally:
        engine.dispose()


def _no_revision_or_identity_changes(settings: Settings, before: dict[str, Any]) -> None:
    later = _observe(settings, expected_revision=SOURCE_REV)
    if later != before:
        raise RuntimeError("shared source cost rows changed during isolated rehearsal")


def _verify_0041_credit_provenance(settings: Settings, counts: dict[str, Any]) -> None:
    """Independent read-only checks on the disposable upgraded database only."""
    url = database_url(settings)
    if not str(url.database).startswith("plm_migration_disposable_") or url.port == 5432:
        raise RuntimeError("credit schema check attempted outside disposable clone")
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            try:
                if conn.execute(text(
                    "SELECT version_num FROM public.alembic_version"
                )).scalars().all() != ["0041_p4_credit_provenance"]:
                    raise RuntimeError("clone is not at the tested 0041 revision")
                for table, credit_columns, constraint in (
                    ("generation_attempts", ("credit_estimate", "actual_credits"),
                     "ck_generation_attempts_credit_provenance"),
                    ("provider_cost_records", ("credits_estimated", "credits_actual"),
                     "ck_provider_cost_records_credit_provenance"),
                ):
                    attrs = {
                        row["column_name"]: row for row in conn.execute(text(
                            "SELECT column_name, is_nullable, column_default "
                            "FROM information_schema.columns "
                            "WHERE table_schema='music' AND table_name=:table"
                        ), {"table": table}).mappings()
                    }
                    for field in (*credit_columns, "credit_receipt_ref"):
                        if attrs[field]["is_nullable"] != "YES":
                            raise RuntimeError("0041 credit values cannot represent UNKNOWN")
                        if attrs[field]["column_default"] is not None:
                            raise RuntimeError("0041 credit value has implicit default")
                    if (
                        attrs["credit_basis"]["is_nullable"] != "NO"
                        or attrs["credit_basis"]["column_default"] is not None
                    ):
                        raise RuntimeError("0041 credit basis not explicitly required")
                    preserved = conn.execute(text(
                        f"SELECT count(*) FROM music.{table} "
                        "WHERE credit_basis='legacy_unverified' AND credit_receipt_ref IS NULL"
                    )).scalar_one()
                    if preserved != counts[table]["count"]:
                        raise RuntimeError("0041 rewrote or mislabelled historic credit records")
                    present = conn.execute(text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE conrelid=CAST(:table AS regclass) AND conname=:name"
                    ), {"table": "music." + table, "name": constraint}).scalar_one()
                    if present != 1:
                        raise RuntimeError("0041 credit provenance constraint missing")
            finally:
                conn.rollback()
    finally:
        engine.dispose()


def rehearse(*, include_credit_revision: bool = False) -> dict[str, Any]:
    source = _source_settings()
    original = _observe(source, expected_revision=SOURCE_REV)
    rest_of_source = read_only_review_snapshot(settings=source)
    cluster = "plm-p4-costs-rehearsal-" + secrets.token_hex(6)
    user = database_url(source).username
    if not user:
        raise RuntimeError("source database user not configured")
    with tempfile.TemporaryDirectory(prefix="plm-p4-costs-isolated-") as tmp:
        archive = Path(tmp) / "source.pgdump"
        _dump(SOURCE_CONTAINER, user, "plm", archive)  # read only on source
        _no_revision_or_identity_changes(source, original)
        created = False
        try:
            password = secrets.token_urlsafe(24)
            port = _new_cluster(cluster, user, password)
            created = True
            target_db = "plm_migration_disposable_" + secrets.token_hex(6)
            isolated = _target_settings(source, port, password, target_db)
            _create_database(isolated, target_db)
            _restore(cluster, user, target_db, archive)
            restored = _observe(isolated, expected_revision=SOURCE_REV)
            if restored != original:
                raise RuntimeError("restored P4 data differs from read-only source")
            clone_rest = read_only_review_snapshot(settings=isolated)
            comparable = lambda value: {
                key: val for key, val in value.items()
                if key not in ("database", "alembic_revision")
            }
            if comparable(rest_of_source) != comparable(clone_rest):
                raise RuntimeError("restored R5 data or access differs from source")
            print("clone_restore: P4 rows and R5 data preserved", flush=True)
            source_columns = {table: value["columns"] for table, value in original.items()}
            config = Config(str(ROOT / "alembic.ini"))
            _upgrade(config, isolated, SOURCE_REV, TARGET_REV)
            upgraded = _observe(
                isolated, expected_revision=TARGET_REV, original_columns=source_columns,
            )
            if upgraded != original:
                raise RuntimeError("0040 changed an existing P4 amount or credit record")
            merged_rest = read_only_review_snapshot(settings=isolated)
            if comparable(merged_rest) != comparable(clone_rest):
                raise RuntimeError("0040 changed unrelated R5 data or table contracts")
            _upgrade(config, isolated, TARGET_REV, TARGET_REV)
            if _observe(isolated, expected_revision=TARGET_REV,
                        original_columns=source_columns) != upgraded:
                raise RuntimeError("0040 reupgrade altered existing cost records")
            tested_rev = TARGET_REV
            if include_credit_revision:
                credit_rev = "0041_p4_credit_provenance"
                _upgrade(config, isolated, TARGET_REV, credit_rev)
                after_credits = _observe(
                    isolated, expected_revision=credit_rev,
                    original_columns=source_columns,
                )
                if after_credits != original:
                    raise RuntimeError("0041 modified historical P4 amounts or credits")
                _verify_0041_credit_provenance(isolated, original)
                if comparable(read_only_review_snapshot(settings=isolated)) != comparable(clone_rest):
                    raise RuntimeError("0041 changed historical R5 data or access")
                _upgrade(config, isolated, credit_rev, credit_rev)
                if _observe(isolated, expected_revision=credit_rev,
                            original_columns=source_columns) != after_credits:
                    raise RuntimeError("0041 reupgrade changed historic cost records")
                tested_rev = credit_rev
            _no_revision_or_identity_changes(source, original)
            if read_only_review_snapshot(settings=source) != rest_of_source:
                raise RuntimeError("connected source data changed during rehearsal")
            return {
                "passed": True,
                "source_revision": SOURCE_REV,
                "clone_revision": tested_rev,
                "historic_credit_values_unchanged": include_credit_revision,
                "all_historic_credit_values_legacy_unverified": include_credit_revision,
                "tables": {table: result["count"] for table, result in original.items()},
                "restored_row_hashes_equal": True,
                "historic_monetary_values_unchanged": True,
                "all_existing_rows_legacy_unverified": True,
                "r5_state_unchanged": True,
                "idempotent": True,
                "shared_database_written": False,
                "new_revision_applied_to_shared_database": False,
                "clone_container_cleanup_attempted": True,
            }
        finally:
            if created:
                subprocess.run(["docker", "stop", cluster], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False, timeout=90)


def main(args: list[str] | None = None) -> int:
    args = sys.argv[1:] if args is None else args
    if args not in ([OPT_IN], [OPT_IN, "--include-0041-credit-provenance"]):
        print("refused: isolated disposable PostgreSQL test requires exact opt-in")
        return 2
    try:
        print(json.dumps(rehearse(
            include_credit_revision=len(args) == 2,
        ), sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - never leak URL, source rows, secrets
        print(json.dumps({"passed": False, "error_class": type(exc).__name__,
                          "note": "source never migrated; isolated test failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

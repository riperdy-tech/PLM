"""Explicitly approved local PLM 0039 -> 0042 migration with retained backup and clone proof.

Owner approval 2026-10-31 ("Back up, then migrate"): the D10 Suno import writes the
0040/0041 music columns. Never use for staging/production or a different database.

Prepare takes a full host-side ``pg_dump`` of the local ``plm`` database, restores it
into a new isolated PostgreSQL 16 container, checks the restore matches the source
table by table, and upgrades the clone to 0042: every pre-existing column of every
table must hash the same after the upgrade. Apply refuses unless the source still
matches the backed-up baseline, upgrades in one transaction through the approved
in-process Alembic connection (``migrations/env.py``), and checks the result equals
the upgraded clone. Run it with the application containers stopped.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

from alembic import command
from alembic.config import Config
from plm_platform.config import Env, Settings, load_settings
from plm_platform.db import build_engine, database_url
from sqlalchemy import text

from tests.support.migration_isolated_rehearsal import (
    ROOT,
    SOURCE_CONTAINER,
    _create_database,
    _inspect,
    _new_cluster,
    _restore,
    _target_settings,
    _upgrade,
)
from tests.support.migration_local_0039_rollout import (
    BACKUP_NAME,
    BACKUP_ROOT,
    MANIFEST_NAME,
    _assert_ignored,
    _full_backup,
    _save_json_once,
    _sha256,
)

PREPARE = "--prepare-approved-local-plm-0042"
APPLY = "--apply-approved-local-plm-0042"
START = "0039_merge_0038_branches"
HEAD = "0042_media_render_evidence"
BACKUP_ID = re.compile(r"0042_local_plm_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{12}\Z")


def _source() -> Settings:
    """The local ``plm`` database in ``plm-postgres-1`` on loopback 5432, PostgreSQL 16."""
    settings = load_settings(dotenv_path=ROOT / ".env")
    if settings.env != Env.LOCAL:
        raise RuntimeError("only ENV=local eligible")
    url = database_url(settings)
    if (
        url.database != "plm" or url.host not in ("127.0.0.1", "localhost")
        or url.port != 5432 or not url.username
    ):
        raise RuntimeError("source identity differs from known local development DB")
    source = _inspect(SOURCE_CONTAINER)
    bindings = source["HostConfig"]["PortBindings"]["5432/tcp"]
    if (
        source["State"]["Running"] is not True
        or not source["Config"]["Image"].startswith("postgres:16")
        or len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1"
        or bindings[0]["HostPort"] != "5432"
    ):
        raise RuntimeError("source PostgreSQL container differs from known development DB")
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            if (
                conn.execute(text("SELECT current_database()")).scalar_one() != "plm"
                or int(conn.execute(text(
                    "SELECT current_setting('server_version_num')")).scalar_one()) // 10000 != 16
            ):
                raise RuntimeError("source database or server version unexpected")
    finally:
        engine.dispose()
    return settings


def _snapshot(
    settings: Settings, columns: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Revision plus, per table, its columns, row count and a content digest.

    With ``columns`` the digest covers only those columns of those tables, so a
    snapshot after an upgrade can prove the pre-existing data is unchanged.
    """
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            revisions = conn.execute(text(
                "SELECT version_num FROM public.alembic_version")).scalars().all()
            listed: dict[str, list[str]] = {}
            for schema, table, column in conn.execute(text(
                "SELECT c.table_schema, c.table_name, c.column_name "
                "FROM information_schema.columns c JOIN information_schema.tables t "
                "USING (table_schema, table_name) WHERE t.table_type = 'BASE TABLE' "
                "AND c.table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY 1, 2, c.ordinal_position"
            )):
                listed.setdefault(f'"{schema}"."{table}"', []).append(column)
            tables: dict[str, Any] = {}
            for name, all_columns in sorted((columns or listed).items()):
                cols = ", ".join(f'"{c}"' for c in all_columns)
                count, digest = conn.execute(text(
                    f"SELECT count(*), md5(coalesce(string_agg(r, E'\\n' ORDER BY r), '')) "
                    f"FROM (SELECT ROW({cols})::text AS r FROM {name}) s"
                )).one()
                tables[name] = {"columns": all_columns, "count": count, "md5": digest}
            conn.rollback()
    finally:
        engine.dispose()
    return {"revisions": revisions, "tables": tables}


def _columns(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    return {name: entry["columns"] for name, entry in snapshot["tables"].items()}


def _preserved(before: dict[str, Any], settings: Settings) -> bool:
    old = _snapshot(settings, _columns(before))
    return bool(old["tables"] == before["tables"])


def _verify_clone(
    settings: Settings, archive: Any, before: dict[str, Any], identifier: str,
) -> dict[str, Any]:
    container = "plm-0042-restore-" + secrets.token_hex(6)
    user = database_url(settings).username
    if not user:
        raise RuntimeError("missing isolated-clone role")
    password = secrets.token_urlsafe(24)
    started = False
    try:
        print("clone_stage: starting separate PostgreSQL", flush=True)
        port = _new_cluster(container, user, password)
        started = True
        target = "plm_migration_disposable_" + identifier.split("_")[-1]
        isolated = _target_settings(settings, port, password, target)
        _create_database(isolated, target)
        print("clone_stage: restoring archived database", flush=True)
        _restore(container, user, target, archive)
        if _snapshot(isolated) != before:
            raise RuntimeError("isolated restored backup differs from source baseline")
        print("clone_restore: every table matches the source", flush=True)
        _upgrade(Config(str(ROOT / "alembic.ini")), isolated, START, HEAD)
        if not _preserved(before, isolated):
            raise RuntimeError("isolated 0042 upgrade changed pre-existing data")
        after = _snapshot(isolated)
        _upgrade(Config(str(ROOT / "alembic.ini")), isolated, HEAD, HEAD)
        if _snapshot(isolated) != after:
            raise RuntimeError("isolated re-upgrade changed data")
        print("isolated_upgrade: 0042 head; preservation and idempotence passed", flush=True)
        return after
    finally:
        if started:
            subprocess.run(
                ["docker", "stop", container], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False, timeout=90,
            )


def _destination(identifier: str) -> Any:
    if not BACKUP_ID.fullmatch(identifier):
        raise RuntimeError("invalid approved backup identifier")
    return BACKUP_ROOT / identifier


def _prepare() -> dict[str, Any]:
    settings = _source()
    before = _snapshot(settings)
    if before["revisions"] != [START]:
        raise RuntimeError("source is not at 0039")
    _assert_ignored()
    identifier = ("0042_local_plm_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                  + "_" + secrets.token_hex(6))
    folder = _destination(identifier)
    folder.mkdir(exist_ok=False)
    archive = folder / BACKUP_NAME
    size = _full_backup(settings, archive)
    if _snapshot(settings) != before:
        raise RuntimeError("source changed during backup; refuse migration")
    print("retained_backup: complete, checksum captured", flush=True)
    clone_after = _verify_clone(settings, archive, before, identifier)
    if _snapshot(settings) != before:
        raise RuntimeError("source changed while verifying cloned backup")
    manifest = {
        "identifier": identifier, "database": "plm",
        "started_revision": START, "target_revision": HEAD,
        "backup_file": BACKUP_NAME, "backup_size": size, "backup_sha256": _sha256(archive),
        "prepared_utc": datetime.now(UTC).isoformat(),
        "source_snapshot": before, "upgraded_clone_snapshot": clone_after,
        "isolated_restore_passed": True, "local_db_applied": False,
    }
    _save_json_once(folder / MANIFEST_NAME, manifest)
    return {
        "prepared": True, "backup_id": identifier, "backup_sha256": manifest["backup_sha256"],
        "backup_bytes": size, "tables": len(before["tables"]),
        "rows": sum(t["count"] for t in before["tables"].values()),
        "restoration_verified": True, "shared_db_changed": False,
    }


def _apply(identifier: str) -> dict[str, Any]:
    settings = _source()
    folder = _destination(identifier)
    archive = folder / BACKUP_NAME
    with (folder / MANIFEST_NAME).open("r", encoding="utf-8") as fh:
        record = json.load(fh)
    if (
        record.get("identifier") != identifier
        or record.get("started_revision") != START or record.get("target_revision") != HEAD
        or record.get("isolated_restore_passed") is not True
        or archive.stat().st_size != record.get("backup_size")
        or _sha256(archive) != record.get("backup_sha256")
    ):
        raise RuntimeError("retained restored backup is missing or changed")
    before = record["source_snapshot"]
    if _snapshot(settings) != before:
        raise RuntimeError("shared database differs from verified pre-migration backup")
    engine = build_engine(settings)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            conn.execute(text("SET LOCAL statement_timeout = '60000ms'"))
            cfg = Config(str(ROOT / "alembic.ini"))
            cfg.attributes["plm_approved_0042_local_connection"] = conn
            print("shared_dev_upgrade: executing approved transactional 0040-0042", flush=True)
            command.upgrade(cfg, HEAD)
            rows = conn.execute(text(
                "SELECT version_num FROM public.alembic_version")).scalars().all()
            if rows != [HEAD]:
                raise RuntimeError("transaction did not converge on exactly one 0042 row")
    finally:
        engine.dispose()
    observed = _snapshot(settings)
    if observed != record["upgraded_clone_snapshot"] or not _preserved(before, settings):
        raise RuntimeError("post-upgrade shared DB differs from verified upgraded clone")
    _save_json_once(folder / "post_upgrade_snapshot.json", observed)
    _save_json_once(folder / "applied.json", {
        "backup_id": identifier, "target": "local:plm", "target_revision": HEAD,
        "completed_utc": datetime.now(UTC).isoformat(), "preservation_verified": True,
    })
    return {
        "applied": True, "target": "local_plm", "revision": HEAD, "backup_id": identifier,
        "post_migration_snapshot_matches_verified_clone": True,
        "pre_existing_data_unchanged": True,
    }


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    try:
        if args == [PREPARE]:
            result = _prepare()
        elif len(args) == 2 and args[0] == APPLY:
            _destination(args[1])
            result = _apply(args[1])
        else:
            print("refused: explicit approved local-PLM-0042 stage required")
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - never print DB records or DSN
        print(json.dumps({
            "passed": False, "error_class": type(exc).__name__, "error": str(exc)[:300],
            "note": "stage stopped; no unverified success or automatic destructive restore",
        }))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

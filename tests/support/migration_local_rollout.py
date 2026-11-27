"""Approved local PLM migration to the checkout's head, with retained backup and clone proof.

The generic form of :mod:`.migration_local_0043_rollout`, behind
``plm migrate --prepare-backup`` and ``plm migrate --approved-backup ID``, so a new
revision needs no edit to ``migrations/env.py``. Never use for staging/production or a
different database.

Prepare takes a full host-side ``pg_dump`` of the local ``plm`` database, restores it
into a new isolated PostgreSQL 16 container, checks the restore matches the source table
by table, and upgrades the clone from the database's current revision to the head: every
pre-existing column of every table must hash the same after the upgrade. Apply refuses
unless the retained backup is intact, the source still matches the backed-up baseline and
the head is the one the clone was upgraded to; it upgrades in one transaction through the
approved in-process Alembic connection (``migrations/env.py``) and checks the result
equals the upgraded clone. Run it with the application containers stopped.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from plm_platform.config import Settings
from plm_platform.db import build_engine, database_url
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tests.support.migration_isolated_rehearsal import (
    ROOT,
    _create_database,
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
from tests.support.migration_local_0043_rollout import _changed, _snapshot, _source

BACKUP_ID = re.compile(r"local_plm_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{12}\Z")
APPROVED_CONNECTION = "plm_approved_local_connection"
APPROVED_STEP = "plm_approved_local_step"


def _config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def _head() -> str:
    heads = ScriptDirectory.from_config(_config()).get_heads()
    if len(heads) != 1:
        raise RuntimeError("checkout must have exactly one migration head")
    return heads[0]


def _destination(identifier: str) -> Path:
    if not BACKUP_ID.fullmatch(identifier):
        raise RuntimeError("invalid approved backup identifier")
    return BACKUP_ROOT / identifier


def _verify_clone(
    settings: Settings, archive: Path, before: dict[str, Any], identifier: str,
    start: str, head: str,
) -> dict[str, Any]:
    container = "plm-rollout-restore-" + secrets.token_hex(6)
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
        # The official image restarts once after initdb, even after pg_isready passed.
        for attempt in range(20):
            try:
                _create_database(isolated, target)
                break
            except OperationalError:
                if attempt == 19:
                    raise
                time.sleep(1)
        print("clone_stage: restoring archived database", flush=True)
        _restore(container, user, target, archive)
        if _snapshot(isolated) != before:
            raise RuntimeError("isolated restored backup differs from source baseline")
        print("clone_restore: every table matches the source", flush=True)
        _upgrade(_config(), isolated, start, head)
        if changed := _changed(before, isolated):
            raise RuntimeError(f"isolated upgrade changed pre-existing data: {changed}")
        after = _snapshot(isolated)
        _upgrade(_config(), isolated, head, head)
        if _snapshot(isolated) != after:
            raise RuntimeError("isolated re-upgrade changed data")
        print(f"isolated_upgrade: {head}; preservation and idempotence passed", flush=True)
        return after
    finally:
        if started:
            subprocess.run(
                ["docker", "stop", container], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False, timeout=90,
            )


def prepare() -> dict[str, Any]:
    """Back up the local ``plm`` DB and prove restore + upgrade on an isolated clone."""
    settings = _source()
    head = _head()
    before = _snapshot(settings)
    if len(before["revisions"]) != 1:
        raise RuntimeError("source does not have exactly one revision row")
    start = before["revisions"][0]
    if start == head:
        raise RuntimeError(f"source is already at {head}; nothing to migrate")
    _assert_ignored()
    identifier = ("local_plm_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                  + "_" + secrets.token_hex(6))
    folder = _destination(identifier)
    folder.mkdir(exist_ok=False)
    archive = folder / BACKUP_NAME
    size = _full_backup(settings, archive)
    if _snapshot(settings) != before:
        raise RuntimeError("source changed during backup; refuse migration")
    print("retained_backup: complete, checksum captured", flush=True)
    clone_after = _verify_clone(settings, archive, before, identifier, start, head)
    if _snapshot(settings) != before:
        raise RuntimeError("source changed while verifying cloned backup")
    manifest = {
        "identifier": identifier, "database": "plm",
        "started_revision": start, "target_revision": head,
        "backup_file": BACKUP_NAME, "backup_size": size, "backup_sha256": _sha256(archive),
        "prepared_utc": datetime.now(UTC).isoformat(),
        "source_snapshot": before, "upgraded_clone_snapshot": clone_after,
        "isolated_restore_passed": True, "local_db_applied": False,
    }
    _save_json_once(folder / MANIFEST_NAME, manifest)
    return {
        "prepared": True, "backup_id": identifier, "backup_sha256": manifest["backup_sha256"],
        "backup_bytes": size, "from": start, "to": head,
        "tables": len(before["tables"]),
        "rows": sum(t["count"] for t in before["tables"].values()),
        "restoration_verified": True, "shared_db_changed": False,
    }


def apply(identifier: str) -> dict[str, Any]:
    """Upgrade the local ``plm`` DB to head, only with an intact, matching backup proof."""
    settings = _source()
    folder = _destination(identifier)
    archive = folder / BACKUP_NAME
    head = _head()
    with (folder / MANIFEST_NAME).open("r", encoding="utf-8") as fh:
        record = json.load(fh)
    start = record.get("started_revision")
    if (
        record.get("identifier") != identifier
        or type(start) is not str
        or record.get("target_revision") != head
        or record.get("isolated_restore_passed") is not True
        or archive.stat().st_size != record.get("backup_size")
        or _sha256(archive) != record.get("backup_sha256")
    ):
        raise RuntimeError("retained restored backup is missing, changed or for another head")
    if (folder / "applied.json").exists():
        raise RuntimeError("this backup was already applied; prepare a new one")
    before = record["source_snapshot"]
    if _snapshot(settings) != before:
        raise RuntimeError("shared database differs from verified pre-migration backup")
    engine = build_engine(settings)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            conn.execute(text("SET LOCAL statement_timeout = '60000ms'"))
            cfg = _config()
            cfg.attributes[APPROVED_CONNECTION] = conn
            cfg.attributes[APPROVED_STEP] = (start, head)
            print(f"shared_dev_upgrade: executing approved transactional {start} -> {head}",
                  flush=True)
            command.upgrade(cfg, head)
            rows = conn.execute(text(
                "SELECT version_num FROM public.alembic_version")).scalars().all()
            if rows != [head]:
                raise RuntimeError("transaction did not converge on exactly one head row")
    finally:
        engine.dispose()
    observed = _snapshot(settings)
    if observed != record["upgraded_clone_snapshot"] or _changed(before, settings):
        raise RuntimeError("post-upgrade shared DB differs from verified upgraded clone")
    _save_json_once(folder / "post_upgrade_snapshot.json", observed)
    _save_json_once(folder / "applied.json", {
        "backup_id": identifier, "target": "local:plm", "target_revision": head,
        "completed_utc": datetime.now(UTC).isoformat(), "preservation_verified": True,
    })
    return {
        "applied": True, "target": "local_plm", "from": start, "revision": head,
        "backup_id": identifier,
        "post_migration_snapshot_matches_verified_clone": True,
        "pre_existing_data_unchanged": True,
    }

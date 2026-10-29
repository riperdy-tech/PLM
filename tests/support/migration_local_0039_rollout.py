"""Explicitly approved local PLM 0039 migration with retained backup and clone proof.

Never use for staging/production or a different database. Prepare creates a
durable gitignored *host-side* backup and verifies restore+upgrade in a new
isolated PostgreSQL 16 container. Apply requires the identical saved baseline,
a real transaction, table locks and an in-process approved Alembic connection.
Only the local plm DB on loopback:5432 is eligible.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
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
    _new_cluster,
    _restore,
    _run,
    _same_preservation,
    _snapshot,
    _source_guard,
    _target_settings,
    _upgrade,
)
from tests.support.migration_review_compare import MERGE, RELEASE, compare_preservation
from tests.support.migration_review_snapshot import read_only_review_snapshot

BACKUP_ROOT = ROOT / "data" / "migration_backups"
PREPARE = "--prepare-approved-local-plm-0039"
APPLY = "--apply-approved-local-plm-0039"
BACKUP_NAME = "source.pgdump"
MANIFEST_NAME = "manifest.json"
BACKUP_ID = re.compile(r"0039_local_plm_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{12}\Z")
REVIEW_SHA = "58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6"


def _source() -> Settings:
    settings = load_settings(dotenv_path=ROOT / ".env")
    if settings.env != Env.LOCAL:
        raise RuntimeError("only ENV=local eligible")
    user, _ = _source_guard(settings)
    if user != database_url(settings).username:
        raise RuntimeError("source role identity changed")
    active = ROOT / "migrations/versions/plm/plm_0038_release_reviews.py"
    if hashlib.sha256(active.read_bytes()).hexdigest() != REVIEW_SHA:
        raise RuntimeError("historical 0038 code drifted")
    return settings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _save_json_once(path: Path, data: dict[str, Any]) -> None:
    # Excludes raw records and passwords. Persist before reporting prepared.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def _destination(identifier: str) -> Path:
    if not BACKUP_ID.fullmatch(identifier):
        raise RuntimeError("invalid approved backup identifier")
    return BACKUP_ROOT / identifier


def _assert_ignored() -> None:
    # Never place a raw PostgreSQL dump in a tracked/project-distributed area.
    if "data/" not in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        raise RuntimeError("backup location is not excluded from Git")
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)


def _full_backup(settings: Settings, destination: Path) -> int:
    user = database_url(settings).username
    if not user:
        raise RuntimeError("missing source role")
    with destination.open("xb") as handle:
        _run(
            ["docker", "exec", SOURCE_CONTAINER, "pg_dump", "--format=custom",
             "--no-password", "-U", user, "-d", "plm"],
            stdout=handle,
        )
        handle.flush()
        os.fsync(handle.fileno())
    size = destination.stat().st_size
    if not 1024 < size < 2_000_000_000:
        raise RuntimeError("source PostgreSQL backup size invalid")
    with destination.open("rb") as handle:
        _run(["docker", "exec", "-i", SOURCE_CONTAINER, "pg_restore", "--list"],
             stdin=handle)
    return size


def _verify_clone(
    settings: Settings, archive: Path, original: dict[str, Any],
    identifier: str,
) -> dict[str, Any]:
    container = "plm-0039-restore-" + secrets.token_hex(6)
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
        print("clone_stage: creating disposable database", flush=True)
        _create_database(isolated, target)
        print("clone_stage: restoring archived database", flush=True)
        _restore(container, user, target, archive)
        print("clone_stage: validating restored source snapshot", flush=True)
        before = _snapshot(isolated, RELEASE)
        if not _same_preservation(original, before):
            raise RuntimeError("isolated restored backup differs from source baseline")
        print("clone_restore: matched original read-only hashes", flush=True)
        _upgrade(Config(str(ROOT / "alembic.ini")), isolated, RELEASE, MERGE)
        after = read_only_review_snapshot(settings=isolated)
        verdict = compare_preservation(original, before, after)
        if not verdict["passed"]:
            raise RuntimeError("real isolated 0039 migration failed preservation")
        _upgrade(Config(str(ROOT / "alembic.ini")), isolated, MERGE, MERGE)
        if read_only_review_snapshot(settings=isolated) != after:
            raise RuntimeError("isolated already-merged upgrade changed data")
        print("isolated_upgrade: one 0039 head; preservation and idempotence passed",
              flush=True)
        return {k: v for k, v in after.items() if k != "database"}
    finally:
        if started:
            subprocess.run(
                ["docker", "stop", container],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=90,
            )


def _prepare() -> dict[str, Any]:
    settings = _source()
    source_before = read_only_review_snapshot(settings=settings)
    if source_before["alembic_revision"] != RELEASE:
        raise RuntimeError("source revision changed before backup")
    _assert_ignored()
    identifier = (
        "0039_local_plm_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + "_" + secrets.token_hex(6)
    )
    folder = _destination(identifier)
    folder.mkdir(exist_ok=False)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass  # Windows ACLs are inherited; not a substitute for disk encryption.
    archive = folder / BACKUP_NAME
    size = _full_backup(settings, archive)
    source_after_dump = read_only_review_snapshot(settings=settings)
    if source_after_dump != source_before:
        raise RuntimeError("source changed during backup; refuse migration")
    print("retained_backup: complete, checksum captured", flush=True)
    cloned_after = _verify_clone(settings, archive, source_before, identifier)
    if read_only_review_snapshot(settings=settings) != source_before:
        raise RuntimeError("source changed while verifying cloned backup")
    manifest: dict[str, Any] = {
        "identifier": identifier,
        "database": "plm",
        "started_revision": RELEASE,
        "target_revision": MERGE,
        "backup_file": BACKUP_NAME,
        "backup_size": size,
        "backup_sha256": _sha256(archive),
        "prepared_utc": datetime.now(UTC).isoformat(),
        "source_snapshot": source_before,
        "merged_clone_snapshot": cloned_after,
        "isolated_restore_passed": True,
        "local_db_applied": False,
    }
    _save_json_once(folder / MANIFEST_NAME, manifest)
    return {
        "prepared": True, "backup_id": identifier,
        "backup_sha256": manifest["backup_sha256"], "backup_bytes": size,
        "restoration_verified": True, "shared_db_changed": False,
        "storage_note": "retained on development host; not offsite",
    }


def _validated_backup(identifier: str) -> dict[str, Any]:
    folder = _destination(identifier)
    archive = folder / BACKUP_NAME
    with (folder / MANIFEST_NAME).open("r", encoding="utf-8") as fh:
        record = json.load(fh)
    if (
        type(record) is not dict or record.get("identifier") != identifier
        or record.get("started_revision") != RELEASE
        or record.get("target_revision") != MERGE
        or record.get("isolated_restore_passed") is not True
        or record.get("local_db_applied") is not False
        or archive.stat().st_size != record.get("backup_size")
        or _sha256(archive) != record.get("backup_sha256")
    ):
        raise RuntimeError("retained restored backup is missing or changed")
    return record


def _apply(identifier: str) -> dict[str, Any]:
    settings = _source()
    record = _validated_backup(identifier)
    original = record["source_snapshot"]
    if read_only_review_snapshot(settings=settings) != original:
        raise RuntimeError("shared database differs from verified pre-migration backup")
    engine = build_engine(settings)
    try:
        with engine.begin() as conn:
            # Lock only the three protected tables; never stop any services or
            # block indefinitely. Concurrent writers make us refuse/rollback.
            conn.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            conn.execute(text("SET LOCAL statement_timeout = '30000ms'"))
            conn.execute(text(
                "LOCK TABLE ops.release_reviews, ops.release_review_events, "
                "youtube.publish_manifests IN SHARE ROW EXCLUSIVE MODE NOWAIT"
            ))
            if read_only_review_snapshot(settings=settings) != original:
                raise RuntimeError("shared data changed after acquiring migration locks")
            cfg = Config(str(ROOT / "alembic.ini"))
            cfg.attributes["plm_approved_0039_local_connection"] = conn
            print("shared_dev_upgrade: executing approved transactional 0039 merge",
                  flush=True)
            command.upgrade(cfg, MERGE)
            rows = conn.execute(text(
                "SELECT version_num FROM public.alembic_version"
            )).scalars().all()
            if rows != [MERGE]:
                raise RuntimeError("transaction did not converge on exactly one 0039 row")
        # SQLAlchemy commits transaction only after all checks above pass.
    finally:
        engine.dispose()
    observed = read_only_review_snapshot(settings=settings)
    expected = record["merged_clone_snapshot"]
    observed_normalized = {k: v for k, v in observed.items() if k != "database"}
    if observed_normalized != expected:
        raise RuntimeError("post-upgrade shared DB differs from verified migrated clone")
    folder = _destination(identifier)
    _save_json_once(folder / "post_upgrade_snapshot.json", observed)
    _save_json_once(folder / "applied.json", {
        "backup_id": identifier, "target": "local:plm",
        "target_revision": MERGE, "completed_utc": datetime.now(UTC).isoformat(),
        "preservation_verified": True,
    })
    return {
        "applied": True, "target": "local_plm", "revision": MERGE,
        "backup_id": identifier, "backup_retained": True,
        "post_migration_snapshot_matches_verified_clone": True,
        "review_rows": observed["release_reviews"]["count"],
        "review_events": observed["release_review_events"]["count"],
        "youtube_manifests": observed["youtube_manifest_common"]["count"],
    }


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    try:
        if args == [PREPARE]:
            result = _prepare()
        elif len(args) == 2 and args[0] == APPLY:
            _destination(args[1])  # Reject unsafe identifiers before DB access.
            result = _apply(args[1])
        else:
            print("refused: explicit approved local-PLM-0039 stage required")
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - never print DB records or DSN
        print(json.dumps({
            "passed": False,
            "error_class": type(exc).__name__,
            "note": "stage stopped; no unverified success or automatic destructive restore",
        }))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

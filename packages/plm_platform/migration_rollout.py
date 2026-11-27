"""Approved local PLM migration to the checkout's head, with retained backup and clone proof.

Behind ``plm migrate --prepare-backup`` and ``plm migrate --approved-backup ID``, so a
new revision needs no edit to ``migrations/env.py``. The generic, runtime form of the
one-off rollouts ``tests/support/migration_local_00{39,42,43}_rollout.py`` (same
procedure; runtime code may not import from ``tests``). Only the local ``plm`` database
in ``plm-postgres-1`` on loopback 5432 is eligible; never staging or production.

Prepare takes a full host-side ``pg_dump`` of the local ``plm`` database, restores it
into a new isolated PostgreSQL 16 container, checks the restore matches the source table
by table, and upgrades the clone from the database's current revision to the head: every
pre-existing column of every table must hash the same after the upgrade. Apply refuses
unless the retained backup is intact, the source still matches the backed-up baseline and
the head is the one the clone was upgraded to; it upgrades in one transaction through the
approved in-process Alembic connection (``migrations/env.py``) and checks the result
equals the upgraded clone. Run it with the application containers stopped. Nothing here
prints a DSN, a password or a row.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from .config import Env, Settings, load_settings
from .db import build_engine, database_url
from .migration_safety import DISPOSABLE_DATABASE_NAME
from .secrets import Secret

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SOURCE_CONTAINER: Final[str] = "plm-postgres-1"
BACKUP_ROOT: Final[Path] = ROOT / "data" / "migration_backups"
BACKUP_NAME: Final[str] = "source.pgdump"
MANIFEST_NAME: Final[str] = "manifest.json"
BACKUP_ID: Final[re.Pattern[str]] = re.compile(r"local_plm_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{12}\Z")
APPROVED_CONNECTION: Final[str] = "plm_approved_local_connection"
APPROVED_STEP: Final[str] = "plm_approved_local_step"
VERSION_TABLE: Final[str] = '"public"."alembic_version"'  # its one row is meant to change

Say = Callable[[str], None]
"""Progress output; the CLI (the edge) passes ``typer.echo``."""
Now = Callable[[], datetime]
"""The wall clock; the CLI (the edge) passes an aware ``datetime.now``."""


def _run(args: list[str], *, stdin: Any = None, stdout: Any = None) -> bytes:
    proc = subprocess.run(
        args, stdin=stdin, stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=240,
    )
    if proc.returncode:
        # Never print raw server output: it can carry records or connection details.
        raise RuntimeError(f"isolated tool failed: {args[0]} operation")
    return proc.stdout if stdout is None else b""


def _inspect(container: str) -> dict[str, Any]:
    items = json.loads(_run(["docker", "inspect", container]))
    if len(items) != 1:
        raise RuntimeError("container inspection unavailable")
    return dict(items[0])


def _config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def _head() -> str:
    heads = ScriptDirectory.from_config(_config()).get_heads()
    if len(heads) != 1:
        raise RuntimeError("checkout must have exactly one migration head")
    return str(heads[0])


def _disposable(name: str | None) -> bool:
    return name is not None and DISPOSABLE_DATABASE_NAME.fullmatch(name) is not None


def _source() -> Settings:
    """The local ``plm`` database in ``plm-postgres-1`` on loopback 5432, PostgreSQL 16."""
    settings = load_settings(dotenv_path=ROOT / ".env")
    if settings.env != Env.LOCAL:
        raise RuntimeError("only ENV=local eligible")
    url = database_url(settings)
    if (
        url.database != "plm" or url.host not in ("127.0.0.1", "localhost")
        or url.port != 5432 or not url.username
        or not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", url.username)
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
            revisions = list(conn.execute(text(
                "SELECT version_num FROM public.alembic_version")).scalars().all())
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


def _changed(before: dict[str, Any], settings: Settings) -> list[str]:
    """Tables whose pre-existing columns no longer hold the same rows."""
    columns = {name: entry["columns"] for name, entry in before["tables"].items()}
    now = _snapshot(settings, columns)["tables"]
    return [name for name, entry in before["tables"].items()
            if name != VERSION_TABLE and now[name] != entry]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _save_json_once(path: Path, data: dict[str, Any]) -> None:
    # Excludes raw records and passwords. Persist before reporting success.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def _destination(identifier: str) -> Path:
    if not BACKUP_ID.fullmatch(identifier):
        raise RuntimeError("invalid approved backup identifier")
    return BACKUP_ROOT / identifier


def _full_backup(settings: Settings, destination: Path) -> int:
    # Never place a raw PostgreSQL dump in a tracked area.
    if "data/" not in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        raise RuntimeError("backup location is not excluded from Git")
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
        _run(["docker", "exec", "-i", SOURCE_CONTAINER, "pg_restore", "--list"], stdin=handle)
    return size


def _new_cluster(container: str, user: str, password: str) -> int:
    """A new throwaway PostgreSQL 16 on a random loopback port; never an existing one."""
    try:
        _inspect(container)
    except RuntimeError:
        pass
    else:
        raise RuntimeError("test container name is already occupied")
    _run([
        "docker", "run", "--detach", "--rm", "--name", container,
        "--network", "bridge", "--label", "plm.rollout.disposable=true",
        "-e", f"POSTGRES_USER={user}", "-e", f"POSTGRES_PASSWORD={password}",
        "-e", "POSTGRES_DB=postgres", "-p", "127.0.0.1::5432",
        "postgres:16",
    ])
    try:
        info = _inspect(container)
        ports = info["NetworkSettings"]["Ports"]["5432/tcp"]
        source_mounts = {mount["Source"] for mount in _inspect(SOURCE_CONTAINER)["Mounts"]}
        if (
            info["Name"] != "/" + container or len(ports) != 1
            or ports[0]["HostIp"] != "127.0.0.1"
            or info["HostConfig"]["NetworkMode"] != "bridge"
            or info["Config"]["Labels"].get("plm.rollout.disposable") != "true"
            or any(m["Source"] in source_mounts for m in info["Mounts"])
        ):
            raise RuntimeError("isolated container lacks expected network/volume isolation")
        port = int(ports[0]["HostPort"])
        if port == 5432 or not 1024 <= port <= 65535:
            raise RuntimeError("isolated container port overlaps shared database")
        for _ in range(60):
            check = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", user, "-d", "postgres"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if check.returncode == 0:
                return port
            time.sleep(0.5)
        raise RuntimeError("isolated PostgreSQL did not become ready")
    except Exception:
        _stop(container)  # created above by this invocation only
        raise


def _stop(container: str) -> None:
    subprocess.run(
        ["docker", "stop", container], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False, timeout=90,
    )


def _target_settings(source: Settings, port: int, password: str, db: str) -> Settings:
    if not _disposable(db):
        raise RuntimeError("target database name is not disposable")
    url = database_url(source).set(host="127.0.0.1", port=port, password=password, database=db)
    return replace(
        source, database_url=Secret(url.render_as_string(hide_password=False)),
        postgres_password=Secret(password),
    )


def _create_database(settings: Settings, db: str) -> None:
    admin = replace(settings, database_url=Secret(
        database_url(settings).set(database="postgres").render_as_string(hide_password=False)
    ))
    engine = build_engine(admin)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            if conn.execute(text("SELECT current_database()")).scalar_one() != "postgres":
                raise RuntimeError("administrative connection unexpectedly redirected")
            if not _disposable(db):
                raise RuntimeError("unsafe disposable name")
            exists = conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :name)"
            ), {"name": db}).scalar_one()
            if exists:
                raise RuntimeError("disposable database name already used")
            conn.execute(text(f'CREATE DATABASE "{db}"'))
    finally:
        engine.dispose()


def _restore(container: str, user: str, database: str, archive: Path) -> None:
    with archive.open("rb") as handle:
        _run([
            "docker", "exec", "-i", container, "pg_restore",
            "--exit-on-error", "--no-owner", "--no-acl", "-U", user, "-d", database,
        ], stdin=handle)


@contextmanager
def _target_environment(settings: Settings) -> Iterator[None]:
    # env.py loads settings itself: point it at the clone in-process, then restore.
    data = {
        "DATABASE_URL": database_url(settings).render_as_string(hide_password=False),
        "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
        "MINIO_ENDPOINT": settings.minio_endpoint,
        "MINIO_ROOT_USER": settings.minio_root_user,
        "MINIO_ROOT_PASSWORD": settings.minio_root_password.get_secret_value(),
        "MINIO_BUCKET": settings.minio_bucket,
        "TEMPORAL_HOST": settings.temporal_host,
    }
    prior = {key: os.environ.get(key) for key in data}
    os.environ.update(data)
    try:
        yield
    finally:
        for key, original in prior.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


def _clone_upgrade(settings: Settings, expected: str, target: str) -> None:
    """Upgrade the disposable clone (raw Alembic's disposable-target rule applies)."""
    if not _disposable(database_url(settings).database):
        raise RuntimeError("unsafe Alembic target")
    if _revisions(settings) != [expected]:
        raise RuntimeError("clone starting revision no longer matches expected")
    with _target_environment(settings):
        command.upgrade(_config(), target)
    if _revisions(settings) != [target]:
        raise RuntimeError("Alembic did not settle on the requested single revision")


def _revisions(settings: Settings) -> list[str]:
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            if conn.execute(text("SELECT current_database()")).scalar_one() != (
                database_url(settings).database
            ):
                raise RuntimeError("connected database changed unexpectedly")
            return list(conn.execute(text(
                "SELECT version_num FROM public.alembic_version")).scalars().all())
    finally:
        engine.dispose()


def _verify_clone(
    settings: Settings, archive: Path, before: dict[str, Any], identifier: str,
    start: str, head: str, say: Say,
) -> dict[str, Any]:
    container = "plm-rollout-restore-" + secrets.token_hex(6)
    user = database_url(settings).username
    if not user:
        raise RuntimeError("missing isolated-clone role")
    password = secrets.token_urlsafe(24)
    started = False
    try:
        say("clone_stage: starting separate PostgreSQL")
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
        say("clone_stage: restoring archived database")
        _restore(container, user, target, archive)
        if _snapshot(isolated) != before:
            raise RuntimeError("isolated restored backup differs from source baseline")
        say("clone_restore: every table matches the source")
        _clone_upgrade(isolated, start, head)
        if changed := _changed(before, isolated):
            raise RuntimeError(f"isolated upgrade changed pre-existing data: {changed}")
        after = _snapshot(isolated)
        _clone_upgrade(isolated, head, head)
        if _snapshot(isolated) != after:
            raise RuntimeError("isolated re-upgrade changed data")
        say(f"isolated_upgrade: {head}; preservation and idempotence passed")
        return after
    finally:
        if started:
            _stop(container)


def prepare(now: Now, say: Say) -> dict[str, Any]:
    """Back up the local ``plm`` DB and prove restore + upgrade on an isolated clone."""
    settings = _source()
    head = _head()
    before = _snapshot(settings)
    if len(before["revisions"]) != 1:
        raise RuntimeError("source does not have exactly one revision row")
    start = before["revisions"][0]
    if start == head:
        raise RuntimeError(f"source is already at {head}; nothing to migrate")
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    identifier = ("local_plm_" + now().strftime("%Y%m%dT%H%M%SZ")
                  + "_" + secrets.token_hex(6))
    folder = _destination(identifier)
    folder.mkdir(exist_ok=False)
    archive = folder / BACKUP_NAME
    size = _full_backup(settings, archive)
    if _snapshot(settings) != before:
        raise RuntimeError("source changed during backup; refuse migration")
    say("retained_backup: complete, checksum captured")
    clone_after = _verify_clone(settings, archive, before, identifier, start, head, say)
    if _snapshot(settings) != before:
        raise RuntimeError("source changed while verifying cloned backup")
    manifest = {
        "identifier": identifier, "database": "plm",
        "started_revision": start, "target_revision": head,
        "backup_file": BACKUP_NAME, "backup_size": size, "backup_sha256": _sha256(archive),
        "prepared_utc": now().isoformat(),
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


def apply(identifier: str, now: Now, say: Say) -> dict[str, Any]:
    """Upgrade the local ``plm`` DB to head, only with an intact, matching backup proof."""
    folder = _destination(identifier)
    settings = _source()
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
            say(f"shared_dev_upgrade: executing approved transactional {start} -> {head}")
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
        "completed_utc": now().isoformat(), "preservation_verified": True,
    })
    return {
        "applied": True, "target": "local_plm", "from": start, "revision": head,
        "backup_id": identifier,
        "post_migration_snapshot_matches_verified_clone": True,
        "pre_existing_data_unchanged": True,
    }

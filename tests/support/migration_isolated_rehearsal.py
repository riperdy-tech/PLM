"""Owner-approved 0038 sibling merge rehearsal on an isolated PostgreSQL container.

Opt in explicitly. The shared PLM database is *only* read through a snapshot and
pg_dump. Never calls Alembic or runs SQL writes against the shared database.
Docker container and all database names are newly created and disposable.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from plm_platform.config import Settings, load_settings
from plm_platform.db import build_engine, database_url
from plm_platform.secrets import Secret
from sqlalchemy import text

from tests.support.migration_clone_preflight import preflight_clone_snapshot
from tests.support.migration_review_compare import (
    MERGE,
    RELEASE,
    YOUTUBE,
    compare_preservation,
)
from tests.support.migration_review_snapshot import read_only_review_snapshot
from tests.support.migration_safety import destructive_migration_name_refusal

ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONTAINER = "plm-postgres-1"
OPT_IN = "--approved-isolated-test-only"


def _run(args: list[str], *, stdin: Any = None, stdout: Any = None) -> bytes:
    proc = subprocess.run(
        args, stdin=stdin, stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=240,
    )
    if proc.returncode:
        # Avoid printing raw server, user records, connection info or credentials.
        raise RuntimeError(f"isolated tool failed: {args[0]} operation")
    return proc.stdout if stdout is None else b""


def _inspect(container: str) -> dict[str, Any]:
    items = json.loads(_run(["docker", "inspect", container]))
    if len(items) != 1:
        raise RuntimeError("container inspection unavailable")
    return items[0]


def _source_guard(settings: Settings) -> tuple[str, dict[str, Any]]:
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
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            if (
                conn.execute(text("SELECT current_database()")).scalar_one() != "plm"
                or conn.execute(text(
                    "SELECT version_num FROM public.alembic_version"
                )).scalar_one() != RELEASE
                or int(conn.execute(text(
                    "SELECT current_setting('server_version_num')"
                )).scalar_one()) // 10000 != 16
            ):
                raise RuntimeError("source database revision or version unexpected")
            # Owner names must survive restoring with --no-owner into a new
            # isolated cluster whose only superuser has this same name.
            catalog = conn.execute(text("""
                SELECT
                    (SELECT bool_and(pg_get_userbyid(c.relowner) = current_user)
                     FROM pg_catalog.pg_class c
                     WHERE c.oid IN (
                         'ops.release_reviews'::regclass,
                         'ops.release_review_events'::regclass,
                         'youtube.publish_manifests'::regclass)
                    ) AS tables_owned,
                    (SELECT bool_and(pg_get_userbyid(n.nspowner) = current_user)
                     FROM pg_catalog.pg_namespace n
                     WHERE n.nspname IN ('ops','youtube')) AS schemas_owned
            """)).one()
            if catalog.tables_owned is not True or catalog.schemas_owned is not True:
                raise RuntimeError("source owner identities cannot safely be reproduced")
            conn.rollback()
    finally:
        engine.dispose()
    return url.username, source


def _dump(container: str, user: str, database: str, destination: Path) -> int:
    with destination.open("wb") as handle:
        _run([
            "docker", "exec", container, "pg_dump", "-Fc",
            "--no-owner", "--no-acl", "-U", user, "-d", database,
        ], stdout=handle)
    size = destination.stat().st_size
    if size < 1024 or size > 2_000_000_000:
        raise RuntimeError("backup empty or exceeds safe bound")
    return size


def _restore(container: str, user: str, database: str, archive: Path) -> None:
    with archive.open("rb") as handle:
        _run([
            "docker", "exec", "-i", container, "pg_restore",
            "--exit-on-error", "--no-owner", "--no-acl",
            "-U", user, "-d", database,
        ], stdin=handle)


def _new_cluster(container: str, user: str, password: str) -> int:
    # Only a never-before-seen name is eligible. No existing container stops.
    try:
        _inspect(container)
    except RuntimeError:
        pass
    else:
        raise RuntimeError("test container name is already occupied")
    _run([
        "docker", "run", "--detach", "--rm", "--name", container,
        "--network", "bridge", "--label", "plm.0038.disposable=true",
        "-e", f"POSTGRES_USER={user}", "-e", f"POSTGRES_PASSWORD={password}",
        "-e", "POSTGRES_DB=postgres", "-p", "127.0.0.1::5432",
        "postgres:16",
    ])
    try:
        info = _inspect(container)
        ports = info["NetworkSettings"]["Ports"]["5432/tcp"]
        source_mounts = {
            mount["Source"] for mount in _inspect(SOURCE_CONTAINER)["Mounts"]
        }
        # Official postgres:16 creates its own anonymous PGDATA volume.
        # It must not reuse ANY existing shared PostgreSQL volume.
        if (
            info["Name"] != "/" + container or len(ports) != 1
            or ports[0]["HostIp"] != "127.0.0.1"
            or info["HostConfig"]["NetworkMode"] != "bridge"
            or info["Config"]["Labels"].get("plm.0038.disposable") != "true"
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
        # This container was created above by this invocation only.
        subprocess.run(
            ["docker", "stop", container], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False, timeout=90,
        )
        raise


def _target_settings(source: Settings, port: int, password: str, db: str) -> Settings:
    if destructive_migration_name_refusal(db, db) is not None:
        raise RuntimeError("target database name is not disposable")
    url = database_url(source).set(
        host="127.0.0.1", port=port, password=password, database=db,
    )
    return replace(
        source, database_url=Secret(url.render_as_string(hide_password=False)),
        postgres_password=Secret(password),
    )


@contextmanager
def _target_environment(settings: Settings) -> Iterator[None]:
    # Alembic env.py calls the actual platform config loader, not test mocks.
    # Its temporary script directory has no .env, so pass required settings
    # only in-process and restore everything after each migration command.
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


def _migration_graph(temp: Path) -> Config:
    tree = temp / "migrations"
    # Test the approved code-installed revision files. Never overwrite them
    # in the temporary graph with older proposal copies, which could hide drift.
    shutil.copytree(ROOT / "migrations", tree)
    from hashlib import sha256

    original = ROOT / "docs/proposals/plm_0038_release_reviews_reference.py"
    installed = tree / "versions/plm/plm_0038_release_reviews.py"
    if sha256(installed.read_bytes()).hexdigest() != (
        "58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6"
    ) or installed.read_bytes() != original.read_bytes():
        raise RuntimeError("installed historical 0038 revision differs from pinned source")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(tree))
    config.set_main_option("version_locations", str(tree / "versions"))
    config.set_main_option("recursive_version_locations", "true")
    return config


def _revision(settings: Settings) -> str:
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            name = conn.execute(text("SELECT current_database()")).scalar_one()
            if name != database_url(settings).database:
                raise RuntimeError("connected database changed unexpectedly")
            revisions = conn.execute(text(
                "SELECT version_num FROM public.alembic_version"
            )).scalars().all()
            if len(revisions) != 1:
                raise RuntimeError("database does not have exactly one revision row")
            return revisions[0]
    finally:
        engine.dispose()


def _upgrade(
    cfg: Config, settings: Settings, expected: str, target: str, *,
    downgrade: bool = False,
) -> None:
    if _revision(settings) != expected:
        raise RuntimeError("target starting revision no longer matches expected")
    if destructive_migration_name_refusal(
        database_url(settings).database, database_url(settings).database,
    ) is not None:
        raise RuntimeError("unsafe Alembic target")
    with _target_environment(settings):
        if downgrade:
            command.downgrade(cfg, target)
        else:
            command.upgrade(cfg, target)
    if _revision(settings) != target:
        raise RuntimeError("Alembic did not settle on the requested single revision")


def _snapshot(settings: Settings, revision: str) -> dict[str, Any]:
    value = read_only_review_snapshot(settings=settings)
    result = preflight_clone_snapshot(
        value, configured_database=database_url(settings).database,
        intended_database=database_url(settings).database,
        starting_revision=revision,
    )
    if not result["passed"]:
        raise RuntimeError("isolated branch snapshot failed strict read-only preflight")
    return value


def _same_preservation(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return {k: v for k, v in a.items() if k != "database"} == {
        k: v for k, v in b.items() if k != "database"
    }


def _seed_youtube_selection(settings: Settings) -> bool:
    engine = build_engine(settings)
    try:
        with engine.begin() as conn:
            if conn.execute(text("SELECT current_database()")).scalar_one() != (
                database_url(settings).database
            ):
                raise RuntimeError("seed target database changed")
            if not conn.execute(text(
                "SELECT version_num FROM public.alembic_version"
            )).scalar_one() == YOUTUBE:
                raise RuntimeError("seed only allowed on YouTube-first clone")
            row = conn.execute(text(
                "SELECT manifest_id FROM youtube.publish_manifests "
                "ORDER BY manifest_id LIMIT 1"
            )).scalar_one_or_none()
            if row is None:
                return False
            conn.execute(text(
                "UPDATE youtube.publish_manifests "
                "SET selection_json=CAST(:selection AS json) "
                "WHERE manifest_id=:manifest_id"
            ), {"selection": json.dumps({"test_selection": True}), "manifest_id": row})
            return True
    finally:
        engine.dispose()


def _create_database(settings: Settings, db: str) -> None:
    # Isolated temporary cluster only; use the admin database for CREATE.
    admin = replace(settings, database_url=Secret(
        database_url(settings).set(database="postgres").render_as_string(
            hide_password=False
        )
    ))
    engine = build_engine(admin)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            if conn.execute(text("SELECT current_database()")).scalar_one() != "postgres":
                raise RuntimeError("administrative connection unexpectedly redirected")
            if not destructive_migration_name_refusal(db, db) is None:
                raise RuntimeError("unsafe disposable name")
            exists = conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :name)"
            ), {"name": db}).scalar_one()
            if exists:
                raise RuntimeError("disposable database name already used")
            conn.execute(text(f'CREATE DATABASE "{db}"'))
    finally:
        engine.dispose()


def rehearse() -> dict[str, Any]:
    source = load_settings(dotenv_path=ROOT / ".env")
    user, _ = _source_guard(source)
    original = read_only_review_snapshot(settings=source)
    if original["alembic_revision"] != RELEASE:
        raise RuntimeError("source changed its historic 0038 revision")

    summary: dict[str, Any] = {
        "shared_database_written": False,
        "migration_installed_in_active_tree": False,
        "source_revision": RELEASE,
        "source_review_rows": original["release_reviews"]["count"],
        "source_review_events": original["release_review_events"]["count"],
        "source_manifest_rows": original["youtube_manifest_common"]["count"],
    }
    token = secrets.token_hex(6)
    container = f"plm-0038-rehearsal-{token}"
    password = secrets.token_urlsafe(24)
    started = False
    with tempfile.TemporaryDirectory(prefix="plm-0038-rehearsal-") as td:
        temp = Path(td)
        src_archive = temp / "source.backup"
        summary["source_backup_bytes"] = _dump(SOURCE_CONTAINER, user, "plm", src_archive)
        if read_only_review_snapshot(settings=source) != original:
            raise RuntimeError("source changed during backup; clone evidence must be recaptured")
        print("backup: shared source dumped read-only and fingerprint stable", flush=True)

        try:
            port = _new_cluster(container, user, password)
            started = True
            summary["isolated_postgres_version"] = 16
            print("isolated PostgreSQL: separate container and port verified", flush=True)
            names = {
                "release": "plm_migration_disposable_0038_release",
                "youtube_src": "plm_migration_disposable_0038_youtube_source",
                "youtube_copy": "plm_migration_disposable_0038_youtube_copy",
            }
            targets = {
                k: _target_settings(source, port, password, db)
                for k, db in names.items()
            }
            for key, name in names.items():
                _create_database(targets[key], name)
            cfg = _migration_graph(temp)

            _restore(container, user, names["release"], src_archive)
            release_before = _snapshot(targets["release"], RELEASE)
            if not _same_preservation(original, release_before):
                raise RuntimeError("restored release branch does not match source")
            print("release-first: backup restoration verified", flush=True)
            _upgrade(cfg, targets["release"], RELEASE, MERGE)
            release_after = read_only_review_snapshot(settings=targets["release"])
            release_result = compare_preservation(original, release_before, release_after)
            if not release_result["passed"]:
                raise RuntimeError("release-first three-way preservation failed: " + repr(
                    release_result["failures"]
                ))
            summary["release_first"] = "passed"
            print("release-first: real sibling upgrade and evidence passed", flush=True)

            # Build historical YouTube-only state on a separate disposable copy.
            # Its six release-review rows are removed from this TEST COPY only.
            _restore(container, user, names["youtube_src"], src_archive)
            if not _same_preservation(
                original, _snapshot(targets["youtube_src"], RELEASE)
            ):
                raise RuntimeError("YouTube source restore is not a faithful copy")
            _upgrade(cfg, targets["youtube_src"], RELEASE, "0037_dashboard_views",
                     downgrade=True)
            _upgrade(cfg, targets["youtube_src"], "0037_dashboard_views", YOUTUBE)
            summary["youtube_selection_fixture_added"] = _seed_youtube_selection(
                targets["youtube_src"]
            )
            youtube_source = _snapshot(targets["youtube_src"], YOUTUBE)
            youtube_archive = temp / "youtube.backup"
            summary["youtube_backup_bytes"] = _dump(
                container, user, names["youtube_src"], youtube_archive
            )
            _restore(container, user, names["youtube_copy"], youtube_archive)
            youtube_before = _snapshot(targets["youtube_copy"], YOUTUBE)
            if not _same_preservation(youtube_source, youtube_before):
                raise RuntimeError("YouTube-first restored clone differs from source")
            print("youtube-first: historical branch and backup restore verified", flush=True)
            _upgrade(cfg, targets["youtube_copy"], YOUTUBE, MERGE)
            youtube_after = read_only_review_snapshot(settings=targets["youtube_copy"])
            youtube_result = compare_preservation(
                youtube_source, youtube_before, youtube_after
            )
            if not youtube_result["passed"]:
                raise RuntimeError("YouTube-first three-way preservation failed: " + repr(
                    youtube_result["failures"]
                ))
            summary["youtube_first"] = "passed"
            print("youtube-first: real sibling upgrade and evidence passed", flush=True)

            for key, after in (
                ("release", release_after), ("youtube_copy", youtube_after),
            ):
                _upgrade(cfg, targets[key], MERGE, MERGE)
                again = read_only_review_snapshot(settings=targets[key])
                if after != again:
                    raise RuntimeError("already-merged upgrade changed snapshot")
            summary["both_idempotent"] = True
            summary["both_single_head"] = True
            summary["restored_backup_and_row_hashes"] = True
            summary["passed"] = True
        finally:
            # Only stop the container started by THIS invocation. --rm deletes it.
            if started:
                subprocess.run(
                    ["docker", "stop", container], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, check=False, timeout=90,
                )
    summary["isolated_container_cleaned"] = started
    return summary


def main(args: list[str] | None = None) -> int:
    chosen = sys.argv[1:] if args is None else args
    if chosen != [OPT_IN]:
        print("refused: explicit isolated-test-only opt-in required")
        return 2
    try:
        output = rehearse()
    except Exception as exc:  # noqa: BLE001 - refuse without logging any secret/data
        print(json.dumps({
            "passed": False, "error_type": type(exc).__name__,
            "note": "rehearsal stopped; inspect isolated diagnostics without secrets",
        }))
        return 1
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

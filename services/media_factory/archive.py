"""After a video is published: copy its renders to the archive drive, then free them here.

Owner decision 2026-11-27: once a video is live on YouTube, its video master and audio
master (1.7-2.6 GB per video) are copied to the archive folder (``E:\\PLM-backups``),
checked against their content hash, and only then deleted from local MinIO. Songs and
thumbnails stay local (later playlists may reuse them). Each archive run also writes a
dump of the PLM database next to them.

The archive folder must hold the marker file ``.plm-archive``. Without it (the USB drive
is unplugged, so Docker mounted an empty folder in its place) nothing is copied or
deleted: the packages wait in the pending list and go with the next run.

"Archived" needs no database column: a package whose renders are gone from MinIO and
whose manifest is in the archive folder is archived.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

ARCHIVE_DIR_VAR = "PLM_ARCHIVE_DIR"
MARKER = ".plm-archive"
RENDER_PREFIX = "media/renders/"
RENDER_NAMES = frozenset({"video_master.mp4", "audio_master.wav"})
KEEP_DB_DUMPS = 10


class ObjectStore(Protocol):
    """The part of the MinIO client the archiver uses."""

    def list_objects(self, bucket_name: str, prefix: str | None = ...,
                     recursive: bool = ...) -> Iterable[Any]: ...

    def fget_object(self, bucket_name: str, object_name: str, file_path: str) -> Any: ...

    def remove_object(self, bucket_name: str, object_name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class PackageArchive:
    media_package_id: str
    status: str
    """``archived`` (copied and freed now), ``already_archived``, ``no_renders`` or
    ``failed`` (kept pending)."""
    freed_bytes: int = 0
    detail: str = ""


@dataclass(slots=True)
class ArchiveRun:
    started_at: str
    finished_at: str | None = None
    drive_ready: bool = False
    reason: str = ""
    database_dump: str = ""
    packages: list[PackageArchive] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class RenderArchiver:
    """Copies a published package's renders to ``archive_dir`` and frees them locally.

    One run at a time, in a background thread (copying gigabytes to a USB drive takes
    minutes); :meth:`request` returns at once. A package is deleted locally only after
    every one of its render objects is on the archive drive with the hash its key pins.
    """

    def __init__(self, *, archive_dir: Path | None, store: Callable[[], ObjectStore],
                 bucket: str, pending_file: Path, clock: Callable[[], datetime],
                 dump_database: Callable[[Path], None] | None = None) -> None:
        self.archive_dir = archive_dir
        self._store = store
        self._bucket = bucket
        self._pending_file = pending_file
        self._clock = clock
        self._dump_database = dump_database
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last: ArchiveRun | None = None

    # -- state -----------------------------------------------------------------------

    def drive_problem(self) -> str | None:
        """Why nothing can be archived now, or ``None``."""
        if self.archive_dir is None:
            return f"archive_not_configured ({ARCHIVE_DIR_VAR} is unset)"
        if not (self.archive_dir / MARKER).is_file():
            return (f"archive_drive_missing (no {MARKER} in {self.archive_dir}: is the "
                    "backup drive plugged in?)")
        return None

    def pending(self) -> list[str]:
        try:
            data = json.loads(self._pending_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []
        return [str(p) for p in data.get("media_package_ids", [])]

    def _save_pending(self, ids: list[str]) -> None:
        self._pending_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._pending_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"media_package_ids": ids}, indent=2), encoding="utf-8")
        os.replace(tmp, self._pending_file)

    def status(self) -> dict[str, Any]:
        problem = self.drive_problem()
        return {"configured": self.archive_dir is not None, "drive_ready": problem is None,
                "reason": problem or "", "pending": self.pending(),
                "running": self._thread is not None and self._thread.is_alive(),
                "last": None if self.last is None else asdict(self.last)}

    # -- requests --------------------------------------------------------------------

    def request(self, package_ids: Iterable[str]) -> str:
        """Queue ``package_ids`` (with every pending one) and start a run if none is going.
        Returns ``started``, ``queued`` or ``not_configured`` (nothing is recorded)."""
        if self.archive_dir is None:
            return "not_configured"
        with self._lock:
            queued = self.pending()
            queued += [p for p in package_ids if p not in queued]
            self._save_pending(queued)
            if self._thread is not None and self._thread.is_alive():
                return "queued"
            self._thread = threading.Thread(target=self._run_until_empty,
                                            name="plm-render-archive", daemon=True)
            self._thread.start()
            return "started"

    def _run_until_empty(self) -> None:
        while True:
            run = self.run_once()
            with self._lock:
                # Go again only for packages queued during this run; a failed one waits
                # for the next request.
                tried = {p.media_package_id for p in run.packages}
                if not run.drive_ready or not set(self.pending()) - tried:
                    self._thread = None
                    return

    def run_once(self) -> ArchiveRun:
        """Archive every pending package now (synchronously)."""
        run = ArchiveRun(started_at=self._clock().isoformat())
        self.last = run
        problem = self.drive_problem()
        if problem is not None:
            run.reason = problem
            run.finished_at = self._clock().isoformat()
            logger.warning("render archive skipped: %s", problem)
            return run
        assert self.archive_dir is not None
        run.drive_ready = True
        targets = self.pending()
        if self._dump_database is not None:
            run.database_dump = self._database_dump()
        objects = self._render_objects()
        for package_id in targets:
            try:
                result = self._archive_package(package_id, objects.get(package_id, []))
            except Exception as exc:  # deliberate: one package's failure keeps it pending
                logger.exception("render archive of %s failed", package_id)
                result = PackageArchive(package_id, "failed", detail=f"{type(exc).__name__}: {exc}")
            run.packages.append(result)
        with self._lock:
            done = {p.media_package_id for p in run.packages if p.status != "failed"}
            self._save_pending([p for p in self.pending() if p not in done])
        run.finished_at = self._clock().isoformat()
        logger.info("render archive run: %s", [(p.media_package_id, p.status)
                                                for p in run.packages])
        return run

    # -- work ------------------------------------------------------------------------

    def _render_objects(self) -> dict[str, list[tuple[str, int]]]:
        """``{package id: [(object key, size)]}`` of every local video/audio master."""
        found: dict[str, list[tuple[str, int]]] = {}
        for obj in self._store().list_objects(self._bucket, prefix=RENDER_PREFIX,
                                              recursive=True):
            key = str(obj.object_name)
            parts = key.split("/")
            # media/renders/<brand>/<package>/.../<name>/<sha256>
            if len(parts) < 6 or parts[-2] not in RENDER_NAMES:
                continue
            found.setdefault(parts[3], []).append((key, int(obj.size or 0)))
        return found

    def _archive_package(self, package_id: str,
                         objects: list[tuple[str, int]]) -> PackageArchive:
        assert self.archive_dir is not None
        folder = self.archive_dir / "videos" / package_id
        manifest_path = folder / "manifest.json"
        if not objects:
            status = "already_archived" if manifest_path.is_file() else "no_renders"
            return PackageArchive(package_id, status)
        manifest: dict[str, Any] = {"media_package_id": package_id, "objects": []}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        known = {o["key"] for o in manifest["objects"]}
        store = self._store()
        for key, size in objects:
            sha = key.rsplit("/", 1)[-1]
            target = folder / key.split("/")[-2] / sha
            if not (target.is_file() and _sha256_file(target) == sha):
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(sha + ".partial")
                store.fget_object(self._bucket, key, str(partial))
                copied = _sha256_file(partial)
                if copied != sha:
                    partial.unlink(missing_ok=True)
                    raise RuntimeError(f"{key}: the copy hashes to {copied}, not {sha}")
                os.replace(partial, target)
            if key not in known:
                manifest["objects"].append({
                    "key": key, "sha256": sha, "bytes": size,
                    "file": target.relative_to(folder).as_posix(),
                    "archived_at": self._clock().isoformat()})
        # The manifest is written before anything is deleted: a stop in between leaves
        # objects both here and on the archive drive, never on neither.
        folder.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp, manifest_path)
        freed = 0
        for key, size in objects:
            store.remove_object(self._bucket, key)
            freed += size
        return PackageArchive(package_id, "archived", freed_bytes=freed)

    def _database_dump(self) -> str:
        assert self.archive_dir is not None and self._dump_database is not None
        folder = self.archive_dir / "db"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = self._clock().strftime("%Y%m%dT%H%M%SZ")
        target = folder / f"plm-{stamp}.dump"
        partial = target.with_name(target.name + ".partial")
        try:
            self._dump_database(partial)
            os.replace(partial, target)
        except Exception as exc:  # deliberate: renders are archived even without a dump
            partial.unlink(missing_ok=True)
            logger.exception("database dump to the archive failed")
            return f"failed: {type(exc).__name__}: {exc}"
        for old in sorted(folder.glob("plm-*.dump"), reverse=True)[KEEP_DB_DUMPS:]:
            old.unlink(missing_ok=True)
        return target.name


def pg_dump_to(database_url: str) -> Callable[[Path], None]:
    """A dump writer using ``pg_dump`` (custom format); the password goes by environment,
    never on the command line."""
    from sqlalchemy.engine import make_url

    url = make_url(database_url)

    def dump(path: Path) -> None:
        env = {**os.environ, "PGPASSWORD": url.password or ""}
        subprocess.run(
            ["pg_dump", "--format=custom", f"--file={path}",
             f"--host={url.host or 'localhost'}", f"--port={url.port or 5432}",
             f"--username={url.username or 'plm'}", f"--dbname={url.database or 'plm'}"],
            env=env, check=True, capture_output=True, timeout=600)

    return dump

"""P6 archives a published video's renders to the backup drive, then frees them (2026-11-27)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from media_factory.archive import MARKER, RenderArchiver

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
PKG = "mpkg_52bd9785e20e51d1"


@dataclass
class _Obj:
    object_name: str
    size: int


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.removed: list[str] = []
        self.corrupt = False

    def add(self, package: str, name: str, data: bytes, claim: str = "c1") -> str:
        sha = hashlib.sha256(data).hexdigest()
        key = f"media/renders/longform_signal/{package}/claims/{claim}/{name}/{sha}"
        self.objects[key] = data
        return key

    def list_objects(self, bucket_name: str, prefix: str | None = None,
                     recursive: bool = False) -> list[_Obj]:
        return [_Obj(k, len(v)) for k, v in self.objects.items()
                if k.startswith(prefix or "")]

    def fget_object(self, bucket_name: str, object_name: str, file_path: str) -> Any:
        data = self.objects[object_name]
        Path(file_path).write_bytes(b"bad" if self.corrupt else data)

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.removed.append(object_name)
        del self.objects[object_name]


def _archiver(tmp_path: Path, store: FakeStore, *, marker: bool = True,
              dumps: list[Path] | None = None) -> RenderArchiver:
    drive = tmp_path / "E" / "PLM-backups"
    drive.mkdir(parents=True)
    if marker:
        (drive / MARKER).write_text("plm archive drive", encoding="utf-8")

    def dump(path: Path) -> None:
        path.write_bytes(b"PGDMP")
        if dumps is not None:
            dumps.append(path)

    return RenderArchiver(archive_dir=drive, store=lambda: store, bucket="plm-media",
                          pending_file=tmp_path / "data" / "archive-pending.json",
                          clock=lambda: NOW, dump_database=dump)


def _run(archiver: RenderArchiver, ids: list[str]) -> None:
    archiver.request(ids)
    thread = archiver._thread
    if thread is not None:
        thread.join(timeout=10)


def test_renders_are_copied_verified_then_freed_and_songs_stay(tmp_path: Path) -> None:
    store = FakeStore()
    video = store.add(PKG, "video_master.mp4", b"video" * 100)
    audio = store.add(PKG, "audio_master.wav", b"audio" * 100)
    store.objects["media/renders/longform_signal/mpkg_x/thumbs/t.png/abc"] = b"thumb"
    song = "music/cand_1/song.wav/deadbeef"
    store.objects[song] = b"song"
    archiver = _archiver(tmp_path, store)

    _run(archiver, [PKG])
    run = archiver.last
    assert run is not None and run.drive_ready
    assert [(p.media_package_id, p.status, p.freed_bytes) for p in run.packages] == [
        (PKG, "archived", 1000)]
    assert sorted(store.removed) == sorted([video, audio])
    assert song in store.objects  # songs are never archived or freed
    folder = archiver.archive_dir / "videos" / PKG  # type: ignore[operator]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["objects"]:
        copy = folder / entry["file"]
        assert hashlib.sha256(copy.read_bytes()).hexdigest() == entry["sha256"]
    assert run.database_dump.startswith("plm-") and archiver.pending() == []

    again = archiver.run_once()
    assert again.packages == []  # nothing pending
    _run(archiver, [PKG])
    assert archiver.last is not None
    assert archiver.last.packages[0].status == "already_archived"


def test_an_unplugged_drive_archives_nothing_and_keeps_the_package_pending(
        tmp_path: Path) -> None:
    store = FakeStore()
    store.add(PKG, "video_master.mp4", b"video")
    archiver = _archiver(tmp_path, store, marker=False)
    _run(archiver, [PKG])
    assert archiver.last is not None
    assert not archiver.last.drive_ready and "archive_drive_missing" in archiver.last.reason
    assert store.removed == [] and archiver.pending() == [PKG]

    # The drive is back: the next video's request takes the pending one along.
    assert archiver.archive_dir is not None
    (archiver.archive_dir / MARKER).write_text("x", encoding="utf-8")
    store.add("mpkg_next", "video_master.mp4", b"next")
    _run(archiver, ["mpkg_next"])
    assert {p.media_package_id: p.status for p in archiver.last.packages} == {
        PKG: "archived", "mpkg_next": "archived"}
    assert archiver.pending() == []


def test_a_copy_that_does_not_match_its_hash_frees_nothing(tmp_path: Path) -> None:
    store = FakeStore()
    store.add(PKG, "video_master.mp4", b"video")
    store.corrupt = True
    archiver = _archiver(tmp_path, store)
    _run(archiver, [PKG])
    assert archiver.last is not None
    assert archiver.last.packages[0].status == "failed"
    assert store.removed == [] and archiver.pending() == [PKG]


def test_unconfigured_archiving_records_nothing(tmp_path: Path) -> None:
    archiver = RenderArchiver(archive_dir=None, store=FakeStore, bucket="b",
                              pending_file=tmp_path / "pending.json", clock=lambda: NOW)
    assert archiver.request([PKG]) == "not_configured"
    assert not (tmp_path / "pending.json").exists()
    assert archiver.status()["configured"] is False


def test_only_the_newest_database_dumps_are_kept(tmp_path: Path) -> None:
    store = FakeStore()
    archiver = _archiver(tmp_path, store)
    db = archiver.archive_dir / "db"  # type: ignore[operator]
    db.mkdir()
    for day in range(1, 13):
        (db / f"plm-202611{day:02d}T000000Z.dump").write_bytes(b"old")
    _run(archiver, [])
    kept = sorted(p.name for p in db.glob("plm-*.dump"))
    assert len(kept) == 10 and kept[-1] == "plm-20261127T090000Z.dump"

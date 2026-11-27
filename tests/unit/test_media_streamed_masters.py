"""Masters go to and from the store as files, never whole in memory (review 2026-11-26,
P6 #25: a 120-minute WAV master is about 2 GB)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from media_factory.artifacts import MinioArtifactStore, StoredArtifact
from media_factory.errors import MediaAssemblyError
from plm_platform.storage import MEDIA_PREFIX, build_key

from tests.support.fake_minio import FakeMinio, store_settings
from tests.unit.test_media_art_inbox_assembly import _assemble, factory  # noqa: F401


def test_the_minio_store_streams_a_file_in_and_out(tmp_path: Path) -> None:
    settings = store_settings()
    fake = FakeMinio()
    store = MinioArtifactStore(settings, client=fake)  # type: ignore[arg-type]
    data = b"RIFF master" * 1000
    source = tmp_path / "master.wav"
    source.write_bytes(data)
    stored = store.put_file("renders/b/p/audio_master.wav", source, content_type="audio/wav")
    digest = hashlib.sha256(data).hexdigest()
    assert (stored.sha256, stored.size) == (digest, len(data))
    key = build_key(MEDIA_PREFIX, "renders/b/p/audio_master.wav", digest)
    assert fake.objects[(settings.minio_bucket, key)] == data
    # Content-addressed and idempotent: the same file again is the same object.
    assert store.put_file("renders/b/p/audio_master.wav", source,
                          content_type="audio/wav") == stored

    target = tmp_path / "back.wav"
    store.fetch_verified_to(stored.uri, stored.sha256, target)
    assert target.read_bytes() == data
    fake.tamper(settings.minio_bucket, key, b"corrupt")
    with pytest.raises(MediaAssemblyError, match="source_checksum_mismatch"):
        store.fetch_verified_to(stored.uri, stored.sha256, tmp_path / "bad.wav")
    assert not (tmp_path / "bad.wav").exists()


def test_rendering_and_art_import_never_hold_a_master_in_memory(
        factory: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from tests.support.media_backend import InMemoryArtifactStore

    original_put = InMemoryArtifactStore.put
    original_fetch = InMemoryArtifactStore.fetch_verified

    def put(self: Any, resource_path: str, data: bytes, *, content_type: str) -> StoredArtifact:
        assert "master" not in resource_path, f"{resource_path} was put from memory"
        return original_put(self, resource_path, data, content_type=content_type)

    def fetch(self: Any, uri: str, sha256: str) -> bytes:
        assert "master" not in uri, f"{uri} was read into memory"
        return original_fetch(self, uri, sha256)

    monkeypatch.setattr(InMemoryArtifactStore, "put", put)
    monkeypatch.setattr(InMemoryArtifactStore, "fetch_verified", fetch)
    pkg, store = _assemble(factory, tmp_path)
    from media_factory.thumbnail_art.importer import import_owner_art
    from plm_platform.clock import SystemClock

    from tests.support.media_backend import StandInToolchain
    from tests.unit.test_thumbnail_art import _png

    folder = tmp_path / pkg.media_package_id
    for name in ("A.png", "B.png", "C.png"):
        (folder / name).write_bytes(_png((1920, 1080)))
    toolchain = StandInToolchain()
    toolchain._durations["audio"] = float(pkg.render_report["timeline_seconds"])
    with factory() as session:
        released = import_owner_art(session, store, toolchain, pkg.media_package_id, folder,
                                    now=SystemClock().now())
        session.commit()
    assert "/owner_art/video_master" in released.video_master_uri

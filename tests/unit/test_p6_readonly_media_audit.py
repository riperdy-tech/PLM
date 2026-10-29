"""Hermetic bounds, fail-closed classifications and no-write audit tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from plm_platform.config import Env

from scripts.audit_p6_media_streams import (
    _download_verified,
    _require_local_dev,
    classify_probe,
)
from services.media_factory.rendering.toolchain import ProbeReport, StreamInfo


def _probe(audio: float | None, video: float | None, container: float = 10.0) -> ProbeReport:
    return ProbeReport(
        format_name="mov,mp4", duration_seconds=container, size_bytes=1200,
        streams=(
            StreamInfo(codec_type="video", codec_name="h264", duration_seconds=video),
            StreamInfo(codec_type="audio", codec_name="aac", duration_seconds=audio),
        ),
    )


@pytest.mark.parametrize(("a", "v", "c", "timeline", "expected"), [
    (10.0, 10.0, 10.0, 10.0, "measured_timing_consistent_only"),
    (8.0, 10.0, 10.0, 10.0, "measured_timing_mismatch"),
    (10.0, 8.0, 10.0, 10.0, "measured_timing_mismatch"),
    (10.0, 10.0, 12.0, 10.0, "measured_timing_mismatch"),
    (None, 10.0, 10.0, 10.0, "unmeasured_stream_or_container"),
    (10.0, 10.0, 10.0, None, "missing_timeline_reference"),
])
def test_ffprobe_classification_is_independent_of_old_ready_status(
    a: float | None, v: float | None, c: float, timeline: float | None, expected: str,
) -> None:
    found = classify_probe(_probe(a, v, c), timeline=timeline,
                           recorded_duration=10.0)
    assert found.status == expected


@pytest.mark.parametrize(("mode", "dbhost", "minio_host"), [
    (Env.PRODUCTION, "127.0.0.1", "127.0.0.1:9000"),
    (Env.LOCAL, "db.prod", "127.0.0.1:9000"),
    (Env.LOCAL, "localhost", "minio.prod:9000"),
])
def test_audit_rejects_nonlocal_credentials_or_endpoints(
    mode: Env, dbhost: str, minio_host: str,
) -> None:
    fake = SimpleNamespace(env=mode, minio_endpoint=minio_host)
    with pytest.raises(RuntimeError, match="refusing non-local"):
        _require_local_dev(fake, dbhost)  # type: ignore[arg-type]


class _Object:
    def __init__(self, data: bytes) -> None:
        self.headers = {"Content-Length": str(len(data))}
        self.data = data
        self.closed = False

    def stream(self, amt: int) -> Any:
        for offset in range(0, len(self.data), amt):
            yield self.data[offset:offset + amt]

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        pass


class _Client:
    def __init__(self, obj: _Object) -> None:
        self.obj = obj

    def stat_object(self, bucket: str, key: str) -> Any:
        assert bucket == "local"
        assert key.startswith("media/")
        return SimpleNamespace(size=len(self.obj.data))

    def get_object(self, bucket: str, key: str) -> _Object:
        assert bucket == "local"
        assert key.startswith("media/")
        return self.obj


def test_audit_download_is_checksum_verified_and_size_bounded(tmp_path: Path) -> None:
    data = b"not a real movie" * 4000
    sha = hashlib.sha256(data).hexdigest()
    uri = f"plm://local/media/test/{sha}"
    settings = SimpleNamespace(minio_bucket="local")
    obj = _Object(data)
    target = tmp_path / "only.mp4"
    assert _download_verified(
        client=_Client(obj), settings=settings, uri=uri, checksum=sha,
        target=target, max_bytes=len(data) + 1,
    ) == "verified_download"
    assert obj.closed
    assert target.read_bytes() == data

    obj = _Object(data)
    result = _download_verified(
        client=_Client(obj), settings=settings, uri=uri, checksum=sha,
        target=tmp_path / "large.mp4", max_bytes=1000,
    )
    assert result == "artifact_exceeds_download_budget"
    assert not obj.closed
    assert not (tmp_path / "large.mp4").exists()

    obj = _Object(data)
    wrong = "f" * 64 if sha != "f" * 64 else "e" * 64
    assert _download_verified(
        client=_Client(obj), settings=settings, uri=uri, checksum=wrong,
        target=tmp_path / "wrong.mp4", max_bytes=len(data) + 1,
    ) == "invalid_artifact_reference"

    # A content-addressed key cannot authorize tampered data returned by storage.
    damaged = data[:-1] + b"X"
    obj = _Object(damaged)
    assert _download_verified(
        client=_Client(obj), settings=settings, uri=uri, checksum=sha,
        target=tmp_path / "tampered.mp4", max_bytes=len(data) + 1,
    ) == "download_digest_mismatch"
    assert obj.closed

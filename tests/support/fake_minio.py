"""An in-process stand-in for the MinIO client, for the R4 artifact-store unit tests.

It implements only the calls :mod:`plm_platform.storage` makes (``bucket_exists``,
``make_bucket``, ``stat_object``, ``put_object``, ``get_object``) so the *production*
P4 store (:class:`MinioArtifactStorage`) and P5 fetcher (:class:`MinioArtifactFetcher`)
run unchanged on top of it: the content addressing, the URI handling and the checksum
verification under test are the real ones. What it does not prove is anything about
MinIO itself; ``tests/integration/test_r4_artifact_flow.py`` does that.

* :meth:`FakeMinio.tamper` overwrites stored bytes in place, as an attacker or a
  corrupted disk would, without touching the key.
* ``unreachable=True`` makes every call raise the urllib3 error a refused connection
  raises.
"""

from __future__ import annotations

import io
from typing import Any, cast

import urllib3
from minio.error import S3Error
from plm_platform.config import Settings, load_settings
from plm_platform.storage import MUSIC_PREFIX, build_key, sha256_digest, to_uri

from tests.support.seams import candidate_event_fields

__all__ = ["STORE_ENV", "FakeMinio", "store_settings", "stored_event_fields"]

STORE_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-unit",
    "TEMPORAL_HOST": "127.0.0.1:9",
}


def store_settings(**extra: str) -> Settings:
    """Platform settings whose MinIO endpoint is a closed port (nothing is reached)."""
    return load_settings(env={**STORE_ENV, **extra})


class _Response:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class FakeMinio:
    """Buckets and objects in a dict; the subset of ``minio.Minio`` the platform uses."""

    def __init__(self, *, unreachable: bool = False) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}
        self.unreachable = unreachable

    def _reach(self) -> None:
        if self.unreachable:
            raise urllib3.exceptions.MaxRetryError(
                cast(Any, None), "/", reason=ConnectionRefusedError("connection refused")
            )

    def _missing(self, bucket: str, key: str) -> S3Error:
        return S3Error(cast(Any, None), "NoSuchKey", "no such key", key, None, None,
                       bucket_name=bucket, object_name=key)

    def bucket_exists(self, bucket: str) -> bool:
        self._reach()
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self._reach()
        self.buckets.add(bucket)

    def stat_object(self, bucket: str, key: str) -> object:
        self._reach()
        if (bucket, key) not in self.objects:
            raise self._missing(bucket, key)
        return object()

    def put_object(self, bucket: str, key: str, data: io.BytesIO, length: int,
                   content_type: str = "application/octet-stream") -> None:
        self._reach()
        self.objects[(bucket, key)] = data.read(length)

    def get_object(self, bucket: str, key: str) -> _Response:
        self._reach()
        if (bucket, key) not in self.objects:
            raise self._missing(bucket, key)
        return _Response(self.objects[(bucket, key)])

    def tamper(self, bucket: str, key: str, data: bytes) -> None:
        """Replace stored bytes under an existing key (the key keeps its digest)."""
        assert (bucket, key) in self.objects, key
        self.objects[(bucket, key)] = data

    def store_audio(self, settings: Settings, candidate_id: str, data: bytes) -> tuple[str, str]:
        """Store ``data`` as P4 would; returns ``(uri, sha256)``."""
        digest = sha256_digest(data)
        key = build_key(MUSIC_PREFIX, f"{candidate_id}/master.wav", digest)
        self.buckets.add(settings.minio_bucket)
        self.objects[(settings.minio_bucket, key)] = data
        return to_uri(key, settings=settings), digest


def stored_event_fields(fake: FakeMinio, settings: Settings, audio: bytes,
                        **overrides: Any) -> dict[str, Any]:
    """A complete C4 event whose artifact URI and sha256 name ``audio`` stored in ``fake``."""
    candidate_id = str(overrides.get("candidate_id", "cand_test_01"))
    uri, digest = fake.store_audio(settings, candidate_id, audio)
    return candidate_event_fields(**{"artifact_uri": uri, "artifact_sha256": digest,
                                     **overrides})

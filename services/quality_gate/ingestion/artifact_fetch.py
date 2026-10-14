"""P5 reads candidate audio by URI and verifies its checksum on read (R4, C9).

P5 never accepts a filesystem path from a caller. The C4 event names the artifact by its
opaque ``artifact_uri`` and its ``artifact_sha256``; an :class:`ArtifactFetcher` returns
the bytes only when they are verified, and :func:`staged_audio` writes them to a private
temporary file that exists only while the evaluators read it.

Every failure is a refusal with a reason code, and nothing is evaluated or recorded.
P5's evaluation record describes bytes P5 actually checked; when the verified bytes
cannot be obtained there is no evaluation to record, and recording an UNKNOWN quarantine
would claim a gate decision about audio nobody examined. The C4 event stays
un-evaluated and can be submitted again once the cause is fixed.

=================================  ====  ============================================
Reason code                        HTTP  Meaning
=================================  ====  ============================================
``artifact_uri_invalid``           422   not a ``plm://`` URI for the configured
                                         bucket, under P4's ``music/`` prefix
``artifact_sha256_mismatch``       422   the event's sha256 differs from the digest
                                         its own URI pins: the event contradicts
                                         itself
``artifact_not_found``             409   no object at the URI (definite: P4's export
                                         names audio that is not stored)
``artifact_integrity_failed``      409   the stored bytes do not hash to the event's
                                         sha256 (tampered or corrupt)
``artifact_store_unavailable``     503   the store could not be reached; retryable
``artifact_store_not_configured``  503   the service has no artifact store
=================================  ====  ============================================
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

import urllib3
from minio import Minio
from minio.error import S3Error
from plm_contracts import ErrorShape
from plm_platform.config import Settings
from plm_platform.storage import (
    MUSIC_PREFIX,
    ContentIntegrityError,
    InvalidArtifactUriError,
    InvalidObjectKeyError,
    ObjectNotFoundError,
    UnknownPrefixError,
    build_client,
    digest_from_key,
    from_uri,
    get_verified,
    sha256_digest,
)

__all__ = [
    "ARTIFACT_INTEGRITY_FAILED",
    "ARTIFACT_NOT_FOUND",
    "ARTIFACT_SHA256_MISMATCH",
    "ARTIFACT_STORE_NOT_CONFIGURED",
    "ARTIFACT_STORE_UNAVAILABLE",
    "ARTIFACT_URI_INVALID",
    "ArtifactFetchRefused",
    "ArtifactFetcher",
    "MinioArtifactFetcher",
    "staged_audio",
]

ARTIFACT_URI_INVALID: Final[str] = "artifact_uri_invalid"
ARTIFACT_SHA256_MISMATCH: Final[str] = "artifact_sha256_mismatch"
ARTIFACT_NOT_FOUND: Final[str] = "artifact_not_found"
ARTIFACT_INTEGRITY_FAILED: Final[str] = "artifact_integrity_failed"
ARTIFACT_STORE_UNAVAILABLE: Final[str] = "artifact_store_unavailable"
ARTIFACT_STORE_NOT_CONFIGURED: Final[str] = "artifact_store_not_configured"

_TRANSPORT_ERRORS: Final[tuple[type[BaseException], ...]] = (
    S3Error,
    urllib3.exceptions.HTTPError,
    OSError,
)


class ArtifactFetchRefused(Exception):
    """The artifact could not be read verified; ``error`` carries the reason code."""

    def __init__(self, code: str, message: str, field_paths: list[str] | None = None) -> None:
        self.error = ErrorShape(code=code, field_paths=field_paths or [], message=message)
        super().__init__(f"{code}: {message}")


@runtime_checkable
class ArtifactFetcher(Protocol):
    """Returns an artifact's bytes only after verifying them against ``artifact_sha256``."""

    def fetch(self, artifact_uri: str, artifact_sha256: str) -> bytes:
        """The verified bytes, or raise :class:`ArtifactFetchRefused`."""
        ...


class MinioArtifactFetcher:
    """Reads P4's artifacts from MinIO with :func:`plm_platform.storage.get_verified`.

    ``client`` is injectable for tests; by default it is the platform client.
    """

    def __init__(self, settings: Settings, *, client: Minio | None = None) -> None:
        self._settings = settings
        self._client = build_client(settings) if client is None else client

    def fetch(self, artifact_uri: str, artifact_sha256: str) -> bytes:
        try:
            key = from_uri(artifact_uri, settings=self._settings)
            pinned = digest_from_key(key)
        except (InvalidArtifactUriError, UnknownPrefixError, InvalidObjectKeyError) as exc:
            raise ArtifactFetchRefused(ARTIFACT_URI_INVALID, str(exc), ["artifact_uri"]) from exc
        if not key.startswith(MUSIC_PREFIX):
            raise ArtifactFetchRefused(
                ARTIFACT_URI_INVALID,
                f"C4 artifacts live under {MUSIC_PREFIX!r}; {artifact_uri!r} does not",
                ["artifact_uri"],
            )
        if pinned != artifact_sha256:
            raise ArtifactFetchRefused(
                ARTIFACT_SHA256_MISMATCH,
                f"event sha256 {artifact_sha256} differs from the digest {pinned} its URI pins",
                ["artifact_sha256", "artifact_uri"],
            )
        try:
            data = get_verified(key, settings=self._settings, client=self._client)
        except ObjectNotFoundError as exc:
            raise ArtifactFetchRefused(ARTIFACT_NOT_FOUND, str(exc), ["artifact_uri"]) from exc
        except ContentIntegrityError as exc:
            raise ArtifactFetchRefused(
                ARTIFACT_INTEGRITY_FAILED, str(exc), ["artifact_sha256"]
            ) from exc
        except _TRANSPORT_ERRORS as exc:
            raise ArtifactFetchRefused(
                ARTIFACT_STORE_UNAVAILABLE, f"{type(exc).__name__}: {exc}"
            ) from exc
        # get_verified checked the key's digest, which equals the event's; the bytes are
        # checked against the event itself as well, so no later change can skip it.
        actual = sha256_digest(data)
        if actual != artifact_sha256:
            raise ArtifactFetchRefused(
                ARTIFACT_INTEGRITY_FAILED,
                f"bytes at {artifact_uri!r} hash to {actual}, event declares {artifact_sha256}",
                ["artifact_sha256"],
            )
        return data


_SIGNATURES: Final[tuple[tuple[bytes, int, str], ...]] = (
    (b"WAVE", 8, ".wav"),
    (b"fLaC", 0, ".flac"),
    (b"OggS", 0, ".ogg"),
    (b"ID3", 0, ".mp3"),
)


def _suffix(data: bytes) -> str:
    for magic, offset, suffix in _SIGNATURES:
        if data[offset : offset + len(magic)] == magic:
            return suffix
    return ".bin"


@contextmanager
def staged_audio(data: bytes, artifact_sha256: str) -> Iterator[Path]:
    """Verified bytes in a private temporary file, removed when the block exits.

    Only the evaluators read it; the file is named by its content address.
    """
    with tempfile.TemporaryDirectory(prefix="plm-qa-") as directory:
        path = Path(directory) / f"{artifact_sha256}{_suffix(data)}"
        path.write_bytes(data)
        yield path

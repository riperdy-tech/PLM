"""Immutable, content-addressed object storage on MinIO (W0.6).

Frozen design rules (P0 §5 C9, ruling R13):

* **One namespace, seven fixed top-level prefixes** (:data:`PREFIXES`) — a
  producer writes under its own prefix and nowhere else.
* **Content addressing.** A key ends in the sha256 of the bytes stored under it:
  :func:`put_content` computes that digest, :func:`put_verified` takes a
  caller-supplied digest and verifies it first, and :func:`get_verified`
  re-computes it on every read.
* **No silent overwrite.** Writing a key that already exists raises
  :class:`ObjectAlreadyExistsError`; :func:`replace_object` is the single,
  explicitly documented, deliberate exception.
* **Artifact URIs are opaque.** Database rows carry ``plm://<bucket>/<key>``
  strings produced by :func:`to_uri`; consumers hand them back to
  :func:`from_uri`, which mechanically checks scheme and bucket and never
  interprets the path — the module does not infer semantics from a path it did
  not build.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from functools import cache
from typing import Final

from minio import Minio
from minio.error import S3Error

from .config import Settings, load_settings

__all__ = [
    "ANALYTICS_PREFIX",
    "EXPERIMENTS_PREFIX",
    "MARKET_PREFIX",
    "MEDIA_PREFIX",
    "MUSIC_PREFIX",
    "PREFIXES",
    "QA_PREFIX",
    "REPORTS_PREFIX",
    "ContentIntegrityError",
    "DigestMismatchError",
    "InvalidArtifactUriError",
    "InvalidObjectKeyError",
    "InvalidResourcePathError",
    "ObjectAlreadyExistsError",
    "ObjectNotFoundError",
    "StorageError",
    "StoredObject",
    "UnknownPrefixError",
    "bucket_name",
    "build_client",
    "build_key",
    "digest_from_key",
    "ensure_bucket",
    "from_uri",
    "get_verified",
    "put_content",
    "put_verified",
    "replace_object",
    "sha256_digest",
    "to_uri",
]

MARKET_PREFIX: Final[str] = "market/"
"""P1 market-intelligence evidence."""

EXPERIMENTS_PREFIX: Final[str] = "experiments/"
"""P3 experiment specs."""

MUSIC_PREFIX: Final[str] = "music/"
"""P4 audio artifacts."""

QA_PREFIX: Final[str] = "qa/"
"""P5 evaluations and evidence."""

MEDIA_PREFIX: Final[str] = "media/"
"""P6 rendered video, audio and thumbnails."""

REPORTS_PREFIX: Final[str] = "reports/"
"""P8 provider reports."""

ANALYTICS_PREFIX: Final[str] = "analytics/"
"""P9 ingested files."""

PREFIXES: Final[tuple[str, ...]] = (
    MARKET_PREFIX,
    EXPERIMENTS_PREFIX,
    MUSIC_PREFIX,
    QA_PREFIX,
    MEDIA_PREFIX,
    REPORTS_PREFIX,
    ANALYTICS_PREFIX,
)
"""The seven fixed top-level prefixes of the single storage namespace, P0 R13."""

_URI_SCHEME: Final[str] = "plm://"
_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")
_MISSING_CODES: Final[frozenset[str]] = frozenset({"NoSuchKey", "NoSuchObject"})
_DEFAULT_CONTENT_TYPE: Final[str] = "application/octet-stream"


class StorageError(Exception):
    """Base class for every storage failure raised by this module."""


class UnknownPrefixError(StorageError, ValueError):
    """The prefix is not one of the seven fixed top-level prefixes."""


class InvalidResourcePathError(StorageError, ValueError):
    """The resource path is not a clean relative object path."""


class InvalidObjectKeyError(StorageError, ValueError):
    """The key is not in the content-addressed format this module builds."""


class InvalidArtifactUriError(StorageError, ValueError):
    """The URI was not built by :func:`to_uri` for the configured bucket."""


class DigestMismatchError(StorageError, ValueError):
    """The caller-supplied digest does not match the content's sha256."""


class ContentIntegrityError(StorageError):
    """Stored bytes no longer hash to the digest pinned in their key."""


class ObjectAlreadyExistsError(StorageError):
    """Refusing to overwrite an existing key — content-addressed objects are immutable."""


class ObjectNotFoundError(StorageError):
    """No object exists at the requested key (or at the key being replaced)."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    """What a successful put wrote: the key, the digest it pins, and its size."""

    key: str
    digest: str
    size: int


def sha256_digest(data: bytes) -> str:
    """Lowercase hex sha256 of ``data`` — the platform's content address."""
    return hashlib.sha256(data).hexdigest()


def _check_digest(digest: str) -> str:
    if _DIGEST_PATTERN.match(digest) is None:
        raise InvalidObjectKeyError(f"not a lowercase sha256 hex digest: {digest!r}")
    return digest


def _check_resource_path(resource_path: str) -> str:
    segments = resource_path.split("/")
    if (
        not resource_path
        or resource_path.startswith("/")
        or "\\" in resource_path
        or any(segment in ("", ".", "..") for segment in segments)
    ):
        raise InvalidResourcePathError(f"not a clean relative resource path: {resource_path!r}")
    return resource_path


def build_key(prefix: str, resource_path: str, digest: str) -> str:
    """Build ``<prefix><resource_path>/<sha256hex>`` — a content-addressed key.

    Raises :class:`UnknownPrefixError`, :class:`InvalidResourcePathError` or
    :class:`InvalidObjectKeyError` rather than producing a key the module could
    not later verify.
    """
    if prefix not in PREFIXES:
        allowed = ", ".join(PREFIXES)
        raise UnknownPrefixError(
            f"unknown storage prefix {prefix!r}: expected one of {allowed}"
        )
    return f"{prefix}{_check_resource_path(resource_path)}/{_check_digest(digest)}"


def digest_from_key(key: str) -> str:
    """The digest a content-addressed key pins (its final path segment).

    Refuses keys outside the seven-prefix namespace — the module never guesses
    at a path it did not build. Raises :class:`UnknownPrefixError` or
    :class:`InvalidObjectKeyError`.
    """
    if not any(key.startswith(prefix) for prefix in PREFIXES):
        raise UnknownPrefixError(f"key {key!r} is outside the platform namespace")
    digest = key.rsplit("/", 1)[-1]
    if _DIGEST_PATTERN.match(digest) is None:
        raise InvalidObjectKeyError(f"key {key!r} does not end in a sha256 digest")
    return digest


def bucket_name(settings: Settings | None = None) -> str:
    """The configured bucket for the active settings."""
    resolved = load_settings() if settings is None else settings
    return resolved.minio_bucket


def to_uri(key: str, *, settings: Settings | None = None) -> str:
    """Opaque artifact URI ``plm://<bucket>/<key>`` for a database row.

    Consumers must treat the result as opaque and hand it back to
    :func:`from_uri`; only this pair may interpret the string.
    """
    return f"{_URI_SCHEME}{bucket_name(settings)}/{key}"


def from_uri(uri: str, *, settings: Settings | None = None) -> str:
    """The key inside a URI produced by :func:`to_uri` for the configured bucket.

    A mechanical check only — scheme and bucket must match; the path inside is
    never interpreted. Raises :class:`InvalidArtifactUriError` otherwise.
    """
    bucket = bucket_name(settings)
    marker = f"{_URI_SCHEME}{bucket}/"
    if not uri.startswith(marker) or len(uri) <= len(marker):
        raise InvalidArtifactUriError(f"not a plm artifact URI for bucket {bucket!r}: {uri!r}")
    return uri[len(marker):]


def _split_endpoint(endpoint: str) -> tuple[str, bool]:
    """``host:port`` keeps TLS off; an explicit ``http(s)://`` scheme decides."""
    if endpoint.startswith("https://"):
        return endpoint.removeprefix("https://"), True
    if endpoint.startswith("http://"):
        return endpoint.removeprefix("http://"), False
    return endpoint, False


def build_client(settings: Settings | None = None) -> Minio:
    """A MinIO client from configuration; credentials never appear in logs."""
    resolved = load_settings() if settings is None else settings
    endpoint, secure = _split_endpoint(resolved.minio_endpoint)
    return Minio(
        endpoint,
        access_key=resolved.minio_root_user,
        secret_key=resolved.minio_root_password.get_secret_value(),
        secure=secure,
    )


@cache
def _default_client() -> Minio:
    return build_client()


def ensure_bucket(*, settings: Settings | None = None, client: Minio | None = None) -> bool:
    """Create the configured bucket when it is missing. Idempotent.

    Returns ``True`` when this call created the bucket, ``False`` when it
    already existed.
    """
    bucket = bucket_name(settings)
    active = _default_client() if client is None else client
    if active.bucket_exists(bucket):
        return False
    active.make_bucket(bucket)
    return True


def _exists(client: Minio, bucket: str, key: str) -> bool:
    try:
        client.stat_object(bucket, key)
    except S3Error as exc:
        if exc.code in _MISSING_CODES:
            return False
        raise
    return True


def _put_new(client: Minio, bucket: str, key: str, data: bytes, content_type: str) -> None:
    # No silent overwrite: existence is checked before every put. This is a
    # stat-then-put (MinIO's Python client exposes no conditional PUT), so a
    # concurrent second writer could still race the check — Wave 0 is a
    # single-writer deployment; the concurrency story arrives with the workers.
    if _exists(client, bucket, key):
        raise ObjectAlreadyExistsError(
            f"refusing to overwrite {key!r}: content-addressed objects are immutable; "
            "use replace_object() for the rare deliberate replacement"
        )
    client.put_object(
        bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
    )


def put_content(
    prefix: str,
    resource_path: str,
    data: bytes,
    *,
    content_type: str = _DEFAULT_CONTENT_TYPE,
    settings: Settings | None = None,
    client: Minio | None = None,
) -> StoredObject:
    """Content-addressed put: the digest is computed from ``data`` right here.

    Raises :class:`ObjectAlreadyExistsError` when the key already exists —
    there is no silent overwrite path anywhere in this module.
    """
    digest = sha256_digest(data)
    key = build_key(prefix, resource_path, digest)
    active = _default_client() if client is None else client
    _put_new(active, bucket_name(settings), key, data, content_type)
    return StoredObject(key=key, digest=digest, size=len(data))


def put_verified(
    prefix: str,
    resource_path: str,
    data: bytes,
    digest: str,
    *,
    content_type: str = _DEFAULT_CONTENT_TYPE,
    settings: Settings | None = None,
    client: Minio | None = None,
) -> StoredObject:
    """Like :func:`put_content`, but for a caller-supplied digest.

    The digest is verified against ``data`` *before* anything is uploaded:
    a mismatch raises :class:`DigestMismatchError` and stores nothing.
    """
    key = build_key(prefix, resource_path, digest)
    actual = sha256_digest(data)
    if actual != digest:
        raise DigestMismatchError(
            f"declared digest {digest!r} does not match content sha256 {actual!r}"
        )
    active = _default_client() if client is None else client
    _put_new(active, bucket_name(settings), key, data, content_type)
    return StoredObject(key=key, digest=digest, size=len(data))


def get_verified(
    key: str,
    *,
    settings: Settings | None = None,
    client: Minio | None = None,
) -> bytes:
    """Fetch an object and verify it: bytes are re-hashed against the pinned digest.

    Raises :class:`ContentIntegrityError` when the bytes no longer hash to the
    digest in the key, and :class:`ObjectNotFoundError` when nothing is stored
    there.
    """
    pinned = digest_from_key(key)
    active = _default_client() if client is None else client
    response = None
    try:
        response = active.get_object(bucket_name(settings), key)
        data: bytes = response.read()
    except S3Error as exc:
        if exc.code in _MISSING_CODES:
            raise ObjectNotFoundError(f"no object at {key!r}") from exc
        raise
    finally:
        if response is not None:
            response.close()
            response.release_conn()
    actual = sha256_digest(data)
    if actual != pinned:
        raise ContentIntegrityError(
            f"content at {key!r} hashes to {actual}, key pins {pinned}"
        )
    return data


def replace_object(
    key: str,
    data: bytes,
    *,
    content_type: str = _DEFAULT_CONTENT_TYPE,
    settings: Settings | None = None,
    client: Minio | None = None,
) -> StoredObject:
    """Deliberately overwrite an existing object — **this breaks immutability**.

    The only path in this module that may overwrite. The key keeps the digest
    segment it was built with, so :func:`get_verified` on that key raises
    :class:`ContentIntegrityError` unless the new bytes hash to the pinned
    digest. Intended for incident response only (legal takedown, corruption
    repair); normal writes go through :func:`put_content`/:func:`put_verified`,
    which refuse existing keys. Raises :class:`ObjectNotFoundError` when there
    is nothing to replace.
    """
    active = _default_client() if client is None else client
    if not _exists(active, bucket_name(settings), key):
        raise ObjectNotFoundError(f"nothing to replace at {key!r}")
    digest = digest_from_key(key)
    active.put_object(
        bucket_name(settings), key, io.BytesIO(data), length=len(data), content_type=content_type
    )
    return StoredObject(key=key, digest=digest, size=len(data))

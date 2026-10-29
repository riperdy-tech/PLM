"""P6's view of the content-addressed artifact store (R4, P0 §5 C9).

P6 reads the cleared track audio and writes what it renders through one protocol,
:class:`ArtifactStore`:

* :meth:`ArtifactStore.fetch_verified` returns the bytes behind a C5 manifest's
  ``artifact_uri`` only when they hash to the manifest's ``artifact_sha256``. A missing
  object, a URI the store did not build, or any checksum mismatch fails closed with a
  :class:`~media_factory.errors.MediaAssemblyError`; an unreachable store is a
  :class:`~media_factory.errors.MediaInfrastructureError`.
* :meth:`ArtifactStore.put` stores bytes under the ``media/`` prefix and returns the URI
  and the sha256 *of the bytes stored*, which is what every P6 record and the C6 payload
  carry.

:class:`MinioArtifactStore` is the production store, written through
:mod:`plm_platform.storage`. Tests use an in-memory store from ``tests/support``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import islice
from typing import Final, Protocol

import urllib3
from minio import Minio
from minio.error import S3Error
from plm_platform.config import Settings
from plm_platform.storage import (
    MEDIA_PREFIX,
    ContentIntegrityError,
    InvalidArtifactUriError,
    InvalidObjectKeyError,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    StorageError,
    UnknownPrefixError,
    build_client,
    build_key,
    digest_from_key,
    from_uri,
    get_verified,
    put_content,
    sha256_digest,
    to_uri,
)

from .errors import MediaAssemblyError, MediaInfrastructureError

__all__ = [
    "SOURCE_ARTIFACT_MISSING",
    "SOURCE_CHECKSUM_MISMATCH",
    "SOURCE_URI_INVALID",
    "STORE_UNAVAILABLE",
    "ArtifactStore",
    "ClaimObjectEvidence",
    "ClaimObjectPage",
    "MinioArtifactStore",
    "StoredArtifact",
    "scoped_artifact_root",
]

SOURCE_ARTIFACT_MISSING: Final[str] = "source_artifact_missing"
SOURCE_CHECKSUM_MISMATCH: Final[str] = "source_checksum_mismatch"
SOURCE_URI_INVALID: Final[str] = "source_uri_invalid"
STORE_UNAVAILABLE: Final[str] = "artifact_store_unavailable"


def scoped_artifact_root(base: str, claim_namespace: str | None) -> str:
    """Keep a claimed render's provisional blobs under one isolated execution prefix.

    Only a 64-character lowercase SHA256 token is accepted; never use raw worker
    identifiers, timestamps or untrusted paths as storage key components. The
    legacy direct-render path keeps its existing keys when there is no claim.
    """
    if claim_namespace is None:
        return base
    if re.fullmatch(r"[0-9a-f]{64}", claim_namespace) is None:
        raise ValueError("render artifact claim namespace must be lowercase SHA256")
    return f"{base}/claims/{claim_namespace}"


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """What P6 stored: the opaque URI, the sha256 of the stored bytes, and their size."""

    uri: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class ClaimObjectEvidence:
    """Object metadata from listing only; SHA256 is a *key claim*, not verified bytes."""

    uri: str
    pinned_sha256: str | None
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class ClaimObjectPage:
    objects: tuple[ClaimObjectEvidence, ...]
    truncated: bool
    # A list operation is not a content-integrity or publication proof.


class ArtifactStore(Protocol):
    def fetch_verified(self, uri: str, sha256: str) -> bytes:
        """The bytes at ``uri``, only if they hash to ``sha256``; otherwise refuse."""
        ...

    def put(self, resource_path: str, data: bytes, *, content_type: str) -> StoredArtifact:
        """Store ``data`` under ``media/<resource_path>/<sha256>``; idempotent per content."""
        ...


class MinioArtifactStore:
    """:class:`ArtifactStore` over :mod:`plm_platform.storage` (MinIO)."""

    def __init__(self, settings: Settings, client: Minio | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else build_client(settings)

    def fetch_verified(self, uri: str, sha256: str) -> bytes:
        try:
            key = from_uri(uri, settings=self._settings)
            pinned = digest_from_key(key)
        except (InvalidArtifactUriError, UnknownPrefixError, InvalidObjectKeyError) as exc:
            raise MediaAssemblyError(SOURCE_URI_INVALID, str(exc)) from exc
        if pinned != sha256:
            raise MediaAssemblyError(
                SOURCE_CHECKSUM_MISMATCH,
                f"{uri} is content-addressed to {pinned}, the manifest declares {sha256}",
            )
        try:
            data = get_verified(key, settings=self._settings, client=self._client)
        except ObjectNotFoundError as exc:
            raise MediaAssemblyError(SOURCE_ARTIFACT_MISSING, str(exc)) from exc
        except ContentIntegrityError as exc:
            raise MediaAssemblyError(SOURCE_CHECKSUM_MISMATCH, str(exc)) from exc
        except (S3Error, StorageError, urllib3.exceptions.HTTPError, OSError) as exc:
            raise MediaInfrastructureError(
                STORE_UNAVAILABLE, f"{type(exc).__name__}: {exc}"
            ) from exc
        if sha256_digest(data) != sha256:  # get_verified checked the key; check the claim too
            raise MediaAssemblyError(SOURCE_CHECKSUM_MISMATCH, f"{uri} does not hash to {sha256}")
        return data

    def list_claim_objects(
        self, *, brand_id: str, media_package_id: str,
        claim_namespace: str, limit: int = 20,
    ) -> ClaimObjectPage:
        """Bounded metadata-only listing of this render's two output trees.

        This never downloads bytes, mutates MinIO, discovers unrelated package
        prefixes, or validates a purported key digest against object content.
        The caller must establish the claim identity from persistent records.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("claim artifact listing limit must be in 1..100")
        for label, value in (("brand_id", brand_id), ("media_package_id", media_package_id)):
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None:
                raise ValueError(f"{label} must be an exact safe identifier")
        roots = (
            scoped_artifact_root(
                f"renders/{brand_id}/{media_package_id}", claim_namespace,
            ),
            scoped_artifact_root(
                f"thumbnails/{brand_id}/{media_package_id}", claim_namespace,
            ),
        )
        # Validate every segment through the platform key builder before
        # passing a prefix to the object store; never accept arbitrary paths.
        prefixes = []
        for root in roots:
            probe = build_key(MEDIA_PREFIX, f"{root}/probe", "0" * 64)
            prefixes.append(probe.removesuffix("probe/" + "0" * 64))
        observed: list[ClaimObjectEvidence] = []
        truncated = False
        try:
            for prefix in prefixes:
                remaining = limit - len(observed)
                if remaining == 0:
                    # Check whether another prefix contains objects before
                    # declaring complete, without scanning unbounded output.
                    extra = next(iter(self._client.list_objects(
                        self._settings.minio_bucket, prefix=prefix, recursive=True,
                    )), None)
                    if extra is not None:
                        truncated = True
                        break
                    continue
                listings = self._client.list_objects(
                    self._settings.minio_bucket, prefix=prefix, recursive=True,
                )
                for item in islice(listings, remaining + 1):
                    key = item.object_name
                    if not isinstance(key, str) or not key.startswith(prefix):
                        raise StorageError("claim object listing returned an out-of-scope key")
                    if len(observed) >= limit:
                        truncated = True
                        break
                    try:
                        digest = digest_from_key(key)
                    except (InvalidObjectKeyError, UnknownPrefixError):
                        digest = None
                    observed.append(ClaimObjectEvidence(
                        uri=to_uri(key, settings=self._settings),
                        pinned_sha256=digest,
                        size_bytes=item.size,
                    ))
                if truncated:
                    break
        except (S3Error, StorageError, urllib3.exceptions.HTTPError, OSError) as exc:
            raise MediaInfrastructureError(
                STORE_UNAVAILABLE, f"claim listing failed: {type(exc).__name__}"
            ) from exc
        return ClaimObjectPage(objects=tuple(observed), truncated=truncated)

    def put(self, resource_path: str, data: bytes, *, content_type: str) -> StoredArtifact:
        digest = sha256_digest(data)
        try:
            stored = put_content(
                MEDIA_PREFIX, resource_path, data, content_type=content_type,
                settings=self._settings, client=self._client,
            )
            key = stored.key
        except ObjectAlreadyExistsError:
            # Content-addressed: an object at this key already holds exactly these bytes.
            key = build_key(MEDIA_PREFIX, resource_path, digest)
        except (S3Error, StorageError, urllib3.exceptions.HTTPError, OSError) as exc:
            raise MediaInfrastructureError(
                STORE_UNAVAILABLE, f"{type(exc).__name__}: {exc}"
            ) from exc
        return StoredArtifact(uri=to_uri(key, settings=self._settings), sha256=digest,
                              size=len(data))

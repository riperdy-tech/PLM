"""Audio artifact probe, intake, and storage (§4.5, §111, R6, R4).

Storage design (R4, P0 §5 C9):

* :class:`MinioArtifactStorage` is the only production store. It writes through
  :mod:`plm_platform.storage`: the key is ``music/<candidate_id>/<name>/<sha256>``, so
  the sha256 *is* the content address, and the returned URI is the opaque
  ``plm://<bucket>/<key>`` built by :func:`plm_platform.storage.to_uri`.
* A store that cannot be reached, or refuses the write, raises
  :class:`ArtifactStorageUnavailable`. No URI is returned for bytes that were not
  stored, and there is no fallback to another store.
* :class:`InMemoryArtifactStorage` is for tests and the hermetic acceptance smoke only.
  Its ``memory://`` URIs are refused by every real consumer, so an in-memory artifact
  can never be mistaken for a stored one.
"""

from __future__ import annotations

import hashlib
import io
import wave
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

import urllib3
from minio import Minio
from minio.error import S3Error
from plm_platform.config import Settings
from plm_platform.storage import (
    MUSIC_PREFIX,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    StorageError,
    build_client,
    build_key,
    ensure_bucket,
    from_uri,
    get_verified,
    put_content,
    sha256_digest,
    to_uri,
)

from ..domain.enums import ArtifactType
from ..domain.ids import artifact_id
from ..domain.models import AudioArtifact, MeasuredAudio

__all__ = [
    "ArtifactStorageProtocol",
    "ArtifactStorageUnavailable",
    "InMemoryArtifactStorage",
    "MinioArtifactStorage",
    "create_audio_artifact",
    "probe_audio_bytes",
]


def probe_audio_bytes(data: bytes, *, mime_type: str = "audio/wav") -> MeasuredAudio:
    """Probe audio byte stream for technical parameters and sha256 hash."""
    sha256_hash = hashlib.sha256(data).hexdigest()
    byte_size = len(data)

    duration_seconds = 0.0
    sample_rate = 44100
    channels = 2
    codec = "unknown"

    if data.startswith(b"RIFF") and b"WAVE" in data[:16]:
        try:
            with wave.open(io.BytesIO(data), "rb") as wf:
                channels = wf.getnchannels()
                sample_rate = wf.getframerate()
                n_frames = wf.getnframes()
                samp_width = wf.getsampwidth()
                codec = f"pcm_s{samp_width * 8}le"
                if sample_rate > 0:
                    duration_seconds = round(float(n_frames) / float(sample_rate), 4)
        except Exception:  # noqa: BLE001
            codec = "pcm_s16le"
    else:
        # Fallback probe for non-WAV streams
        codec = "compressed"

    return MeasuredAudio(
        duration_seconds=duration_seconds,
        sample_rate=sample_rate,
        channels=channels,
        codec=codec,
        byte_size=byte_size,
        sha256=sha256_hash,
        mime_type=mime_type,
        probe_version="probe-v1",
    )


def create_audio_artifact(
    *,
    candidate_id: str,
    object_uri: str,
    measured: MeasuredAudio,
    artifact_type: ArtifactType = ArtifactType.FULL_MIX,
    created_at: datetime,
) -> AudioArtifact:
    """Create an AudioArtifact model instance with probed audio properties."""
    return AudioArtifact(
        artifact_id=artifact_id(candidate_id, artifact_type.value, measured.sha256),
        candidate_id=candidate_id,
        artifact_type=artifact_type,
        object_uri=object_uri,
        sha256=measured.sha256,
        mime_type=measured.mime_type,
        byte_size=measured.byte_size,
        duration_seconds=measured.duration_seconds,
        sample_rate=measured.sample_rate,
        channels=measured.channels,
        codec=measured.codec,
        probe_version=measured.probe_version,
        created_at=created_at,
    )


@runtime_checkable
class ArtifactStorageProtocol(Protocol):
    """Persists audio artifact bytes and hands back the URI they are stored under."""

    def put_artifact(
        self,
        *,
        candidate_id: str,
        name: str,
        data: bytes,
        mime_type: str = "audio/wav",
    ) -> str:
        """Store the bytes and return their URI, or raise; never a URI for unstored bytes."""
        ...

    def get_artifact(self, object_uri: str) -> bytes | None:
        """The verified bytes at ``object_uri``; ``None`` when nothing is stored there."""
        ...


class ArtifactStorageUnavailable(RuntimeError):
    """The artifact store could not be reached or refused the operation (fail closed)."""


_MEMORY_SCHEME: Final[str] = "memory://"
_TRANSPORT_ERRORS: Final[tuple[type[BaseException], ...]] = (
    S3Error,
    urllib3.exceptions.HTTPError,
    OSError,
)


def _resource_path(candidate_id: str, name: str) -> str:
    return f"{candidate_id}/{name}"


class InMemoryArtifactStorage:
    """Test-only store: content-addressed like MinIO, but with ``memory://`` URIs.

    Not a fallback: no configured service builds one. Real consumers resolve URIs with
    :func:`plm_platform.storage.from_uri`, which refuses this scheme.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def put_artifact(
        self,
        *,
        candidate_id: str,
        name: str,
        data: bytes,
        mime_type: str = "audio/wav",
    ) -> str:
        key = build_key(MUSIC_PREFIX, _resource_path(candidate_id, name), sha256_digest(data))
        uri = f"{_MEMORY_SCHEME}{key}"
        self.store[uri] = data
        return uri

    def get_artifact(self, object_uri: str) -> bytes | None:
        return self.store.get(object_uri)


class MinioArtifactStorage:
    """The production P4 artifact store: content-addressed objects on MinIO (C9).

    ``client`` is injectable for tests; by default it is the platform client built from
    ``settings``. The bucket is created on first write when missing (idempotent).
    """

    def __init__(self, settings: Settings, *, client: Minio | None = None) -> None:
        self._settings = settings
        self._client = build_client(settings) if client is None else client
        self._bucket_ready = False

    def _ensure_bucket(self) -> None:
        if not self._bucket_ready:
            ensure_bucket(settings=self._settings, client=self._client)
            self._bucket_ready = True

    def put_artifact(
        self,
        *,
        candidate_id: str,
        name: str,
        data: bytes,
        mime_type: str = "audio/wav",
    ) -> str:
        """Store the bytes under their content address and return the ``plm://`` URI.

        Re-storing identical bytes for the same candidate and name finds the same key
        and returns the same URI (the key pins the digest, so the object is the same
        content). Raises :class:`ArtifactStorageUnavailable` when the store is
        unreachable or refuses the write.
        """
        resource = _resource_path(candidate_id, name)
        try:
            self._ensure_bucket()
            stored = put_content(
                MUSIC_PREFIX,
                resource,
                data,
                content_type=mime_type,
                settings=self._settings,
                client=self._client,
            )
            key = stored.key
        except ObjectAlreadyExistsError:
            key = build_key(MUSIC_PREFIX, resource, sha256_digest(data))
        except _TRANSPORT_ERRORS as exc:
            raise ArtifactStorageUnavailable(
                f"artifact store unavailable writing {resource!r}: {type(exc).__name__}: {exc}"
            ) from exc
        return to_uri(key, settings=self._settings)

    def get_artifact(self, object_uri: str) -> bytes | None:
        """The bytes at ``object_uri``, re-hashed against the digest the key pins.

        ``None`` when no object is stored there. A URI this store did not build raises
        :class:`plm_platform.storage.InvalidArtifactUriError`; bytes that no longer match
        their digest raise :class:`plm_platform.storage.ContentIntegrityError`; an
        unreachable store raises :class:`ArtifactStorageUnavailable`.
        """
        key = from_uri(object_uri, settings=self._settings)
        try:
            return get_verified(key, settings=self._settings, client=self._client)
        except ObjectNotFoundError:
            return None
        except StorageError:
            raise
        except _TRANSPORT_ERRORS as exc:
            raise ArtifactStorageUnavailable(
                f"artifact store unavailable reading {key!r}: {type(exc).__name__}: {exc}"
            ) from exc

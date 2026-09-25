"""Audio artifact probe, intake, and storage management (§4.5, §111, R6)."""

from __future__ import annotations

import hashlib
import io
import wave
from datetime import UTC, datetime
from typing import Any, Protocol, cast, runtime_checkable

from ..domain.enums import ArtifactType
from ..domain.ids import artifact_id
from ..domain.models import AudioArtifact, MeasuredAudio

__all__ = [
    "ArtifactStorageProtocol",
    "InMemoryArtifactStorage",
    "LocalArtifactStorage",
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
        created_at=datetime.now(UTC),
    )


@runtime_checkable
class ArtifactStorageProtocol(Protocol):
    """Protocol for persisting audio artifact bytes to an object store."""

    def put_artifact(
        self,
        *,
        candidate_id: str,
        name: str,
        data: bytes,
        mime_type: str = "audio/wav",
    ) -> str:
        """Store artifact bytes and return a canonical object URI."""
        ...

    def get_artifact(self, object_uri: str) -> bytes | None:
        """Retrieve artifact bytes by URI."""
        ...


class InMemoryArtifactStorage:
    """In-memory store for testing and hermetic offline operation."""

    def __init__(self, prefix: str = "s3://plm-artifacts/music") -> None:
        self.prefix = prefix
        self.store: dict[str, bytes] = {}

    def put_artifact(
        self,
        *,
        candidate_id: str,
        name: str,
        data: bytes,
        mime_type: str = "audio/wav",
    ) -> str:
        uri = f"{self.prefix}/{candidate_id}/{name}"
        self.store[uri] = data
        return uri

    def get_artifact(self, object_uri: str) -> bytes | None:
        return self.store.get(object_uri)


class LocalArtifactStorage:
    """Filesystem-backed artifact storage."""

    def __init__(self, base_dir: str) -> None:
        import pathlib

        self.base_path = pathlib.Path(base_dir)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def put_artifact(
        self,
        *,
        candidate_id: str,
        name: str,
        data: bytes,
        mime_type: str = "audio/wav",
    ) -> str:
        dest_dir = self.base_path / "music" / candidate_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / name
        dest_file.write_bytes(data)
        return dest_file.as_uri()

    def get_artifact(self, object_uri: str) -> bytes | None:
        import pathlib
        import urllib.parse

        parsed = urllib.parse.urlparse(object_uri)
        path = pathlib.Path(urllib.parse.unquote(parsed.path))
        if path.exists():
            return path.read_bytes()
        return None


class MinioArtifactStorage:
    """MinIO / S3 backed artifact storage."""

    def __init__(
        self,
        *,
        endpoint: str = "localhost:9000",
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin",
        bucket: str = "plm-artifacts",
        secure: bool = False,
    ) -> None:
        self.bucket = bucket
        self.client: Any = None
        try:
            from minio import Minio

            self.client = Minio(
                endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=secure,
            )
        except Exception:  # noqa: BLE001
            self.client = None

    def put_artifact(
        self,
        *,
        candidate_id: str,
        name: str,
        data: bytes,
        mime_type: str = "audio/wav",
    ) -> str:
        key = f"music/{candidate_id}/{name}"
        if self.client is not None:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
            self.client.put_object(
                self.bucket,
                key,
                io.BytesIO(data),
                len(data),
                content_type=mime_type,
            )
        return f"s3://{self.bucket}/{key}"

    def get_artifact(self, object_uri: str) -> bytes | None:
        if not object_uri.startswith(f"s3://{self.bucket}/"):
            return None
        key = object_uri[len(f"s3://{self.bucket}/") :]
        if self.client is None:
            return None
        try:
            resp = self.client.get_object(self.bucket, key)
            return cast(bytes, resp.read())
        except Exception:  # noqa: BLE001
            return None

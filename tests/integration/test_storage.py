"""W0.6 integration: immutability, verification and no-overwrite on dev MinIO.

Skips with an explicit reason when MinIO is unreachable — start it with
``docker compose up -d --wait minio``. Objects written here are best-effort
removed again via the raw client when each test finishes.
"""

from __future__ import annotations

import dataclasses
import io
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from minio import Minio
from minio.error import S3Error
from plm_platform.config import Settings, load_settings
from plm_platform.storage import (
    MEDIA_PREFIX,
    ContentIntegrityError,
    DigestMismatchError,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    build_client,
    build_key,
    ensure_bucket,
    from_uri,
    get_verified,
    put_content,
    put_verified,
    replace_object,
    sha256_digest,
    to_uri,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclasses.dataclass
class Run:
    """Per-test unique resource-path namespace plus the keys to clean up."""

    run_id: str
    keys: list[str] = dataclasses.field(default_factory=list)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return load_settings(dotenv_path=REPO_ROOT / ".env")


@pytest.fixture(scope="module")
def client(settings: Settings) -> Minio:
    minio = build_client(settings)
    try:
        minio.bucket_exists(settings.minio_bucket)
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        pytest.skip(
            f"minio not reachable at {settings.minio_endpoint} - {type(exc).__name__}: {exc}; "
            "start it with: docker compose up -d --wait minio"
        )
    ensure_bucket(settings=settings, client=minio)
    return minio


@pytest.fixture
def run(settings: Settings, client: Minio) -> Iterator[Run]:
    state = Run(run_id=uuid.uuid4().hex)
    yield state
    for key in state.keys:
        try:
            client.remove_object(settings.minio_bucket, key)
        except S3Error:  # cleanup is best-effort
            pass


def _exists(client: Minio, bucket: str, key: str) -> bool:
    try:
        client.stat_object(bucket, key)
    except S3Error as exc:
        if exc.code in ("NoSuchKey", "NoSuchObject"):
            return False
        raise
    return True


def test_ensure_bucket_creates_missing_buckets_and_is_idempotent(
    settings: Settings, client: Minio
) -> None:
    fresh = dataclasses.replace(settings, minio_bucket=f"plm-it-{uuid.uuid4().hex}")
    try:
        assert ensure_bucket(settings=fresh, client=client) is True
        assert ensure_bucket(settings=fresh, client=client) is False
        assert client.bucket_exists(fresh.minio_bucket) is True
    finally:
        client.remove_bucket(fresh.minio_bucket)


def test_put_content_is_content_addressed_and_get_verified_round_trips(
    run: Run, settings: Settings, client: Minio
) -> None:
    data = b"\x00\x01waveform" * 64
    path = f"integration/w06/{run.run_id}/master.wav"
    stored = put_content(MEDIA_PREFIX, path, data, settings=settings, client=client)
    run.keys.append(stored.key)
    assert stored.key == build_key(MEDIA_PREFIX, path, sha256_digest(data))
    assert stored.digest == sha256_digest(data)
    assert stored.size == len(data)
    assert get_verified(stored.key, settings=settings, client=client) == data
    assert from_uri(to_uri(stored.key, settings=settings), settings=settings) == stored.key


def test_second_put_to_the_same_key_fails_loudly(
    run: Run, settings: Settings, client: Minio
) -> None:
    data = b"same-bytes"
    path = f"integration/w06/{run.run_id}/same.bin"
    stored = put_content(MEDIA_PREFIX, path, data, settings=settings, client=client)
    run.keys.append(stored.key)
    with pytest.raises(ObjectAlreadyExistsError, match="refusing to overwrite"):
        put_content(MEDIA_PREFIX, path, data, settings=settings, client=client)


def test_put_verified_refuses_a_wrong_digest_before_uploading(
    run: Run, settings: Settings, client: Minio
) -> None:
    data = b"declared elsewhere"
    wrong = sha256_digest(b"a different payload")
    path = f"integration/w06/{run.run_id}/wrong.bin"
    with pytest.raises(DigestMismatchError, match="does not match content sha256"):
        put_verified(MEDIA_PREFIX, path, data, wrong, settings=settings, client=client)
    assert _exists(client, settings.minio_bucket, build_key(MEDIA_PREFIX, path, wrong)) is False


def test_get_verified_detects_tampering(
    run: Run, settings: Settings, client: Minio
) -> None:
    data = b"original bytes"
    path = f"integration/w06/{run.run_id}/tampered.bin"
    stored = put_content(MEDIA_PREFIX, path, data, settings=settings, client=client)
    run.keys.append(stored.key)
    tampered = b"tampered bytes"
    client.put_object(
        settings.minio_bucket, stored.key, io.BytesIO(tampered), length=len(tampered)
    )
    with pytest.raises(ContentIntegrityError, match=sha256_digest(tampered)):
        get_verified(stored.key, settings=settings, client=client)


def test_missing_object_raises_object_not_found(run: Run, settings: Settings, client: Minio) -> None:
    key = build_key(
        MEDIA_PREFIX, f"integration/w06/{run.run_id}/absent.bin", sha256_digest(b"never written")
    )
    with pytest.raises(ObjectNotFoundError, match="no object at"):
        get_verified(key, settings=settings, client=client)


def test_replace_object_is_the_only_deliberate_overwrite(
    run: Run, settings: Settings, client: Minio
) -> None:
    original = b"v1 original"
    path = f"integration/w06/{run.run_id}/replace.bin"
    stored = put_content(MEDIA_PREFIX, path, original, settings=settings, client=client)
    run.keys.append(stored.key)

    absent = build_key(MEDIA_PREFIX, f"integration/w06/{run.run_id}/never.bin", sha256_digest(b"x"))
    with pytest.raises(ObjectNotFoundError, match="nothing to replace"):
        replace_object(absent, b"x", settings=settings, client=client)

    replaced = replace_object(stored.key, b"v2 replacement", settings=settings, client=client)
    assert replaced.key == stored.key
    # The key still pins the original digest: read verification flags the break.
    with pytest.raises(ContentIntegrityError):
        get_verified(stored.key, settings=settings, client=client)
    # Restoring bytes that hash to the pinned digest makes the object verifiable again.
    replace_object(stored.key, original, settings=settings, client=client)
    assert get_verified(stored.key, settings=settings, client=client) == original

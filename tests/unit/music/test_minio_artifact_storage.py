"""R4: P4 stores audio in the MinIO artifact store and fails closed without one.

The production :class:`MinioArtifactStorage` runs over :class:`tests.support.fake_minio.
FakeMinio` (an in-process stand-in for the MinIO client) or over a real ``minio.Minio``
pointed at a closed port. These tests prove P4's content addressing, its URIs, its
fail-closed behaviour and its wiring; MinIO itself is exercised by
``tests/integration/test_r4_artifact_flow.py``.
"""

from __future__ import annotations

import inspect
from hashlib import sha256
from typing import Any

import pytest
import sqlalchemy as sa
import urllib3
from fastapi import FastAPI
from fastapi.testclient import TestClient
from minio import Minio
from plm_platform.config import SERVICE_TOKEN_HEADER
from plm_platform.storage import ContentIntegrityError, InvalidArtifactUriError, from_uri

from services.music_factory.api.app import create_app
from services.music_factory.api.routes import build_router
from services.music_factory.config import load_music_factory_settings
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.providers.mock_provider import synthesize_mock_wav
from services.music_factory.storage.artifacts import (
    ArtifactStorageUnavailable,
    InMemoryArtifactStorage,
    MinioArtifactStorage,
)
from services.music_factory.storage.repositories import list_candidates_by_track
from tests.support.fake_minio import FakeMinio, store_settings
from tests.support.music_db import create_music_engine, music_session
from tests.unit.music.test_api_routes import SAMPLE_C3_SPEC

TOKEN = "r4-music-token"
HEADERS = {SERVICE_TOKEN_HEADER: TOKEN}


def _storage(fake: FakeMinio) -> MinioArtifactStorage:
    return MinioArtifactStorage(store_settings(), client=fake)  # type: ignore[arg-type]


def test_put_is_content_addressed_and_returns_the_opaque_plm_uri() -> None:
    """Proves: the key is music/<candidate>/<name>/<sha256>, the URI is plm://<bucket>/<key>,
    the bytes read back verified, and re-storing identical bytes returns the same URI."""
    fake = FakeMinio()
    storage = _storage(fake)
    audio = synthesize_mock_wav(duration_seconds=1.0)
    uri = storage.put_artifact(candidate_id="cand_r4", name="master.wav", data=audio)

    key = from_uri(uri, settings=store_settings())
    assert key == f"music/cand_r4/master.wav/{sha256(audio).hexdigest()}"
    assert fake.objects == {("plm-unit", key): audio}
    assert storage.get_artifact(uri) == audio
    assert storage.put_artifact(candidate_id="cand_r4", name="master.wav", data=audio) == uri


def test_an_unreachable_store_fails_closed_with_no_uri() -> None:
    """Proves: when MinIO cannot be reached, put raises ArtifactStorageUnavailable (no URI
    is invented) and get raises rather than answering 'not found'."""
    storage = _storage(FakeMinio(unreachable=True))
    with pytest.raises(ArtifactStorageUnavailable):
        storage.put_artifact(candidate_id="c", name="master.wav", data=b"RIFF")
    uri = f"plm://plm-unit/music/c/master.wav/{'0' * 64}"
    with pytest.raises(ArtifactStorageUnavailable):
        storage.get_artifact(uri)


def test_the_real_minio_client_on_a_closed_port_fails_closed() -> None:
    """Proves: the error a real ``minio.Minio`` raises for a refused connection is mapped
    to ArtifactStorageUnavailable (not only the fake's)."""
    client = Minio("127.0.0.1:9", access_key="plm", secret_key="x", secure=False,
                   http_client=urllib3.PoolManager(retries=urllib3.Retry(total=0),
                                                   timeout=urllib3.Timeout(connect=1, read=1)))
    storage = MinioArtifactStorage(store_settings(), client=client)
    with pytest.raises(ArtifactStorageUnavailable):
        storage.put_artifact(candidate_id="c", name="master.wav", data=b"RIFF")


def test_reads_verify_the_checksum_and_refuse_foreign_uris() -> None:
    """Proves: tampered bytes raise ContentIntegrityError (never returned), a missing
    object is None, and an in-memory URI is not a URI this store accepts."""
    fake = FakeMinio()
    storage = _storage(fake)
    audio = synthesize_mock_wav(duration_seconds=1.0)
    uri = storage.put_artifact(candidate_id="cand_t", name="master.wav", data=audio)
    fake.tamper("plm-unit", from_uri(uri, settings=store_settings()), audio + b"x")
    with pytest.raises(ContentIntegrityError):
        storage.get_artifact(uri)
    assert storage.get_artifact(f"plm://plm-unit/music/nope/master.wav/{'1' * 64}") is None
    memory_uri = InMemoryArtifactStorage().put_artifact(
        candidate_id="cand_t", name="master.wav", data=audio)
    with pytest.raises(InvalidArtifactUriError):
        storage.get_artifact(memory_uri)


def test_the_engine_has_no_default_store() -> None:
    """Proves: GenerationEngine cannot be built without an explicit store, so no caller
    silently gets the in-memory one."""
    assert inspect.signature(GenerationEngine).parameters["storage"].default is (
        inspect.Parameter.empty)


def _execute(client: TestClient) -> tuple[int, dict[str, Any]]:
    plan = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=HEADERS)
    assert plan.status_code == 200
    plan_id = plan.json()["plan_id"]
    res = client.post(f"/music/plans/{plan_id}/execute", json=SAMPLE_C3_SPEC, headers=HEADERS)
    return res.status_code, res.json()


def _candidates(engine: sa.Engine) -> list[Any]:
    session = music_session(engine)
    try:
        return list_candidates_by_track(session, "trk_api_01")
    finally:
        session.close()


def test_a_configured_api_uses_minio_and_never_the_in_memory_store() -> None:
    """Proves: given MinIO settings and no injected store, the API's store is the MinIO
    store bound to those settings, never InMemoryArtifactStorage."""
    engine = create_music_engine(static_pool=True)
    settings = load_music_factory_settings(platform=store_settings(PLM_SERVICE_TOKEN=TOKEN))
    app = create_app(session_factory=lambda: music_session(engine), settings=settings)
    store = app.state.artifact_storage
    assert isinstance(store, MinioArtifactStorage)
    assert not isinstance(store, InMemoryArtifactStorage)


def test_generation_fails_closed_when_minio_is_down() -> None:
    """Proves: when the store cannot be reached mid-run, execute answers 503
    artifact_store_unavailable and commits no candidate (nothing points at audio that
    was not stored)."""
    engine = create_music_engine(static_pool=True)
    settings = load_music_factory_settings(platform=store_settings(PLM_SERVICE_TOKEN=TOKEN))
    app = create_app(session_factory=lambda: music_session(engine), settings=settings,
                     storage=_storage(FakeMinio(unreachable=True)))
    status, body = _execute(TestClient(app))
    assert status == 503
    assert body["detail"]["code"] == "artifact_store_unavailable"
    assert _candidates(engine) == []


def test_without_any_store_generation_is_refused() -> None:
    """Proves: a router with no store refuses to generate (503
    artifact_store_not_configured) instead of keeping audio in memory."""
    engine = create_music_engine(static_pool=True)
    settings = load_music_factory_settings(platform=store_settings(PLM_SERVICE_TOKEN=TOKEN))
    app = FastAPI()
    app.include_router(build_router(session_factory=lambda: music_session(engine),
                                    settings=settings, storage=None))
    status, body = _execute(TestClient(app))
    assert status == 503
    assert body["detail"]["code"] == "artifact_store_not_configured"
    assert _candidates(engine) == []

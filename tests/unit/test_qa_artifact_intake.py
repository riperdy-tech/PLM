"""R4: P5 reads candidate audio by URI, verifies it on read, and never takes a path.

The production :class:`MinioArtifactFetcher` and the production ``POST /qa/evaluations``
route run over :class:`tests.support.fake_minio.FakeMinio` (an in-process stand-in for
the MinIO client), so the URI handling, checksum verification and refusals under test
are the real ones. Real MinIO is exercised by ``tests/integration/test_r4_artifact_flow.py``.
"""

from __future__ import annotations

import math
import struct
import wave
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import urllib3
from fastapi.testclient import TestClient
from minio import Minio
from plm_contracts.seams.c4 import CandidateReadyPayload
from plm_platform.config import SERVICE_TOKEN_HEADER
from plm_platform.storage import build_key, from_uri, sha256_digest, to_uri
from quality_gate.api.app import create_app
from quality_gate.config import load_quality_gate_settings
from quality_gate.domain.ids import evaluation_id
from quality_gate.ingestion.artifact_fetch import (
    ArtifactFetchRefused,
    MinioArtifactFetcher,
    staged_audio,
)
from quality_gate.storage.repositories import get_candidate_evaluation

from tests.support.fake_minio import FakeMinio, store_settings, stored_event_fields
from tests.support.qa_db import create_qa_engine, qa_session

TOKEN = "r4-qa-token"
HEADERS = {SERVICE_TOKEN_HEADER: TOKEN}
SETTINGS = store_settings(PLM_SERVICE_TOKEN=TOKEN)


def _wav() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        frames = bytearray()
        for i in range(44100):
            v = int(14000.0 * math.sin(2.0 * math.pi * 440.0 * i / 44100))
            frames.extend(struct.pack("<hh", v, v))
        wf.writeframes(frames)
    return buffer.getvalue()


AUDIO = _wav()


@pytest.fixture
def store() -> FakeMinio:
    return FakeMinio()


@pytest.fixture
def qa_engine() -> sa.Engine:
    return create_qa_engine(static_pool=True)


@pytest.fixture
def client(store: FakeMinio, qa_engine: sa.Engine) -> TestClient:
    app = create_app(
        session_factory=lambda: qa_session(qa_engine),
        settings=load_quality_gate_settings(platform=SETTINGS),
        artifact_fetcher=MinioArtifactFetcher(SETTINGS, client=store),  # type: ignore[arg-type]
    )
    return TestClient(app)


def _event(store: FakeMinio, **overrides: Any) -> dict[str, Any]:
    return CandidateReadyPayload.build(
        **stored_event_fields(store, SETTINGS, AUDIO, **overrides)).to_wire()


def _post(client: TestClient, event: dict[str, Any], **extra: Any) -> Any:
    return client.post("/qa/evaluations", json={"candidate_event": event, **extra},
                       headers=HEADERS)


def _nothing_recorded(qa_engine: sa.Engine, candidate_id: str) -> bool:
    session = qa_session(qa_engine)
    try:
        eid = evaluation_id(candidate_id, "p5-v1", "candidate_ready")
        return get_candidate_evaluation(session, eid) is None
    finally:
        session.close()


def test_a_caller_given_audio_path_is_refused(client: TestClient, store: FakeMinio) -> None:
    """Proves: the production route has no path input; ``audio_path`` is refused with 422
    before anything is read or evaluated."""
    res = _post(client, _event(store), audio_path="/etc/passwd")
    assert res.status_code == 422


def test_evaluates_the_bytes_read_by_uri(client: TestClient, store: FakeMinio,
                                         qa_engine: sa.Engine) -> None:
    """Proves: an event whose URI and sha256 name stored audio is evaluated from those
    bytes, with no path supplied by anyone, and the evaluation is recorded. The verdict
    itself depends on the configured P5 detectors and is not what this proves."""
    res = _post(client, _event(store, candidate_id="cand_r4_ok"))
    assert res.status_code == 201, res.text
    assert res.json()["disposition"] in {"PASSED", "PASSED_WITH_WARNINGS", "QUARANTINED",
                                         "REJECTED"}
    assert not _nothing_recorded(qa_engine, "cand_r4_ok")


def test_tampered_bytes_are_refused_and_never_evaluated(
    client: TestClient, store: FakeMinio, qa_engine: sa.Engine
) -> None:
    """Proves: when the stored object no longer matches the event's sha256, P5 refuses
    with 409 artifact_integrity_failed and records no evaluation, decision or manifest."""
    event = _event(store, candidate_id="cand_r4_tamper")
    key = from_uri(event["artifact_uri"], settings=SETTINGS)
    store.tamper(SETTINGS.minio_bucket, key, AUDIO[:-2] + b"\x00\x01")
    res = _post(client, event)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "artifact_integrity_failed"
    assert _nothing_recorded(qa_engine, "cand_r4_tamper")
    manifest = client.get("/qa/release-manifests/cand_r4_tamper", headers=HEADERS)
    assert manifest.status_code == 404


def test_an_event_whose_sha256_contradicts_its_uri_is_refused(
    client: TestClient, store: FakeMinio, qa_engine: sa.Engine
) -> None:
    """Proves: an event declaring a sha256 other than the digest its URI pins is refused
    with 422 artifact_sha256_mismatch, even though the stored object itself is intact."""
    event = _event(store, candidate_id="cand_r4_mismatch")
    event["artifact_sha256"] = sha256_digest(b"other bytes")
    res = _post(client, event)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "artifact_sha256_mismatch"
    assert _nothing_recorded(qa_engine, "cand_r4_mismatch")


def test_a_missing_object_is_refused(client: TestClient, qa_engine: sa.Engine) -> None:
    """Proves: a well-formed URI with nothing stored at it is refused with 409
    artifact_not_found; no evaluation is recorded."""
    digest = sha256_digest(AUDIO)
    uri = to_uri(build_key("music/", "cand_r4_missing/master.wav", digest), settings=SETTINGS)
    event = CandidateReadyPayload.build(**stored_event_fields(
        FakeMinio(), SETTINGS, AUDIO, candidate_id="cand_r4_missing",
        artifact_uri=uri, artifact_sha256=digest)).to_wire()
    res = _post(client, event)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "artifact_not_found"
    assert _nothing_recorded(qa_engine, "cand_r4_missing")


def test_an_unreachable_store_is_a_retryable_refusal(store: FakeMinio, client: TestClient,
                                                     qa_engine: sa.Engine) -> None:
    """Proves: when MinIO cannot be reached P5 answers 503 artifact_store_unavailable
    (retryable by the pipeline) and records nothing; it never evaluates without bytes."""
    event = _event(store, candidate_id="cand_r4_down")
    store.unreachable = True
    res = _post(client, event)
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "artifact_store_unavailable"
    assert _nothing_recorded(qa_engine, "cand_r4_down")


@pytest.mark.parametrize("uri", [
    "memory://music/cand/master.wav/" + "0" * 64,  # InMemoryArtifactStorage's test URIs
    "s3://plm-unit/music/cand/master.wav",
    "plm://another-bucket/music/cand/master.wav/" + "0" * 64,
    "plm://plm-unit/media/cand/master.wav/" + "0" * 64,  # not P4's prefix
])
def test_uris_p5_cannot_resolve_are_refused(client: TestClient, store: FakeMinio,
                                            uri: str) -> None:
    """Proves: only a plm:// URI for the configured bucket under music/ is read; anything
    else (a test-only memory URI included) is refused with 422 artifact_uri_invalid."""
    event = _event(store, candidate_id="cand_r4_uri")
    event["artifact_uri"] = uri
    event["artifact_sha256"] = "0" * 64
    res = _post(client, event)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "artifact_uri_invalid"


def test_without_a_store_evaluation_is_refused(qa_engine: sa.Engine, store: FakeMinio) -> None:
    """Proves: a P5 router with no artifact store refuses with 503
    artifact_store_not_configured instead of evaluating anything."""
    from fastapi import FastAPI
    from quality_gate.api.routes import build_router

    app = FastAPI()
    app.include_router(build_router(session_factory=lambda: qa_session(qa_engine),
                                    settings=load_quality_gate_settings(platform=SETTINGS),
                                    artifact_fetcher=None))
    res = _post(TestClient(app), _event(store, candidate_id="cand_r4_none"))
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "artifact_store_not_configured"


def test_a_configured_service_reads_from_minio(qa_engine: sa.Engine) -> None:
    """Proves: with MinIO settings and nothing injected, P5's fetcher is the MinIO one."""
    app = create_app(session_factory=lambda: qa_session(qa_engine),
                     settings=load_quality_gate_settings(platform=SETTINGS))
    assert isinstance(app.state.artifact_fetcher, MinioArtifactFetcher)


def test_a_retried_request_replays_the_stored_evaluation(
    client: TestClient, store: FakeMinio
) -> None:
    """Proves: a second POST of the same event returns the stored evaluation (same id
    and disposition) without reading the store again, so a retried pipeline step does
    not evaluate twice."""
    event = _event(store, candidate_id="cand_r4_replay")
    first = _post(client, event)
    assert first.status_code == 201
    store.unreachable = True  # a re-read would now be refused
    second = _post(client, event)
    assert second.status_code == 201
    assert second.json() == first.json()


def test_the_real_minio_client_on_a_closed_port_is_unavailable() -> None:
    """Proves: the error a real ``minio.Minio`` raises for a refused connection maps to
    artifact_store_unavailable (not only the fake's)."""
    client = Minio("127.0.0.1:9", access_key="plm", secret_key="x", secure=False,
                   http_client=urllib3.PoolManager(retries=urllib3.Retry(total=0),
                                                   timeout=urllib3.Timeout(connect=1, read=1)))
    digest = sha256_digest(AUDIO)
    uri = to_uri(build_key("music/", "c/master.wav", digest), settings=SETTINGS)
    with pytest.raises(ArtifactFetchRefused) as refused:
        MinioArtifactFetcher(SETTINGS, client=client).fetch(uri, digest)
    assert refused.value.error.code == "artifact_store_unavailable"


def test_staged_audio_exists_only_inside_the_block() -> None:
    """Proves: the verified bytes are written to a private temp file named by their
    content address, and the file is gone once the evaluators are done."""
    digest = sha256_digest(AUDIO)
    with staged_audio(AUDIO, digest) as path:
        assert path.name == f"{digest}.wav"
        assert path.read_bytes() == AUDIO
        staged = Path(path)
    assert not staged.exists()
    assert not staged.parent.exists()

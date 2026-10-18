"""Unit tests for Phase P5 FastAPI endpoints and service-token auth (P5 §94, R7, R8, R15)."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from plm_contracts.seams.c4 import CandidateReadyPayload
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.exceptions import StaticReviewerDirectory
from quality_gate.api.app import create_app
from quality_gate.config import load_quality_gate_settings
from quality_gate.ingestion.artifact_fetch import MinioArtifactFetcher

from tests.support.fake_minio import FakeMinio, stored_event_fields
from tests.support.qa_db import create_qa_engine, qa_session
from tests.support.rights_detectors import (
    register_controlled_references,
    synthetic_music,
    write_wav,
)
from tests.support.seams import candidate_event, lineage_fields, rights_fields

_TEST_TOKEN = "test-qa-secret-token"
ENV_WITH_TOKEN = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": _TEST_TOKEN,
}


@pytest.fixture
def qa_engine():
    engine = create_qa_engine(static_pool=True)
    register_controlled_references(engine, now=datetime(2026, 9, 1, tzinfo=UTC))
    return engine


@pytest.fixture
def store() -> FakeMinio:
    return FakeMinio()


@pytest.fixture
def qa_client(qa_engine, store):
    platform = load_settings(env=ENV_WITH_TOKEN)
    settings = load_quality_gate_settings(
        platform=platform,
        reviewers=StaticReviewerDirectory(
            {"auditor_bob": ["rights_reviewer"], "admin_root": ["admin", "rights_counsel"]}
        ),
    )
    app = create_app(
        session_factory=lambda: qa_session(qa_engine),
        settings=settings,
        artifact_fetcher=MinioArtifactFetcher(platform, client=store),  # type: ignore[arg-type]
    )
    return TestClient(app)


def _stored_event(store: FakeMinio, wav_path: Path, **fields: object) -> dict:
    """The C4 wire for ``fields`` with the WAV stored in ``store`` under its URI."""
    platform = load_settings(env=ENV_WITH_TOKEN)
    return CandidateReadyPayload.build(
        **stored_event_fields(store, platform, wav_path.read_bytes(), **fields)
    ).to_wire()


def _make_temp_wav(duration: float = 1.0) -> Path:
    # Music-like audio, unrelated to the registered reference: a pure tone has too few
    # landmarks for the fingerprint detector to judge (it would be UNKNOWN, ADR-0034).
    path = Path(tempfile.gettempdir()) / f"api_test_{int(duration * 1000)}.wav"
    return write_wav(path, synthetic_music(seed=17, seconds=duration))


def test_qa_health_is_public(qa_client) -> None:
    res = qa_client.get("/qa/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "quality_gate"}


def test_qa_endpoints_require_service_token(qa_client) -> None:
    res = qa_client.get("/qa/quarantine")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "invalid_service_token"

    # With invalid token
    res_bad = qa_client.get("/qa/quarantine", headers={SERVICE_TOKEN_HEADER: "wrong-token"})
    assert res_bad.status_code == 401
    assert res_bad.json()["detail"]["code"] == "invalid_service_token"


def test_evaluate_and_get_manifest_flow(qa_client, qa_engine, store) -> None:
    wav_path = _make_temp_wav(duration=1.5)
    try:
        headers = {SERVICE_TOKEN_HEADER: _TEST_TOKEN}
        eval_payload = {
            "candidate_event": _stored_event(
                store,
                wav_path,
                candidate_id="cand_api_01",
                artifact_id="art_api_01",
                track_spec_id="spec_api_01",
                lineage=lineage_fields(experiment_id="exp_api_01", content_arm_id="arm_api_01"),
                prompt_text="relaxing lo-fi chill beat",
            ),
        }

        # 1. Post evaluation
        post_res = qa_client.post("/qa/evaluations", json=eval_payload, headers=headers)
        assert post_res.status_code == 201
        data = post_res.json()
        assert data["candidate_id"] == "cand_api_01"
        assert data["disposition"] == "PASSED"
        assert data["release_manifest_id"] is not None

        # 2. Get evaluation by ID
        eval_id = data["evaluation_id"]
        get_res = qa_client.get(f"/qa/evaluations/{eval_id}", headers=headers)
        assert get_res.status_code == 200
        assert get_res.json()["evaluation_id"] == eval_id

        # 3. Retrieve release manifest (Seam C5 contract endpoint)
        man_res = qa_client.get("/qa/release-manifests/cand_api_01", headers=headers)
        assert man_res.status_code == 200
        manifest_data = man_res.json()
        assert manifest_data["candidate_id"] == "cand_api_01"
        assert manifest_data["disposition"] == "PASSED"
        assert manifest_data["ai_disclosure_required"] is True
        assert manifest_data["p5_manifest_id"].startswith("rel_man_")

    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_quarantine_and_resolve_flow(qa_client, qa_engine, store) -> None:
    wav_path = _make_temp_wav(duration=1.5)
    try:
        headers = {SERVICE_TOKEN_HEADER: _TEST_TOKEN}
        # Declared commercial use unknown -> triggers quarantine
        eval_payload = {
            "candidate_event": _stored_event(
                store,
                wav_path,
                candidate_id="cand_quar_api_01",
                artifact_id="art_api_02",
                track_spec_id="spec_api_02",
                lineage=lineage_fields(experiment_id="exp_api_02", content_arm_id="arm_api_02"),
                rights=rights_fields(commercial_use_status="unknown"),
            ),
        }

        # 1. Post evaluation -> Quarantined
        post_res = qa_client.post("/qa/evaluations", json=eval_payload, headers=headers)
        assert post_res.status_code == 201
        data = post_res.json()
        assert data["disposition"] == "QUARANTINED"
        case_id = data["quarantine_case_id"]
        assert case_id is not None
        assert data["quarantine_reason_codes"] == ["UNKNOWN_COMMERCIAL_LICENSE"]

        # 2. List quarantine cases
        list_res = qa_client.get("/qa/quarantine", headers=headers)
        assert list_res.status_code == 200
        cases = list_res.json()
        assert any(c["case_id"] == case_id for c in cases)

        # 3. Resolve quarantine case with CLEARED_BY_OVERRIDE
        holds = next(c for c in cases if c["case_id"] == case_id)["holds"]
        assert [(h["reason_code"], h["hold_class"]) for h in holds] == [
            ("UNKNOWN_COMMERCIAL_LICENSE", "REVIEWABLE_QUARANTINE")
        ]
        resolve_payload = {
            "resolution": "CLEARED_BY_OVERRIDE",
            "auditor_id": "auditor_bob",
            "notes": "Verified licensing manually with provider.",
            "reviewer_role": "rights_reviewer",
            "evidence": {
                "provider_license_document_ref": "doc://provider/license/42",
                "license_terms_version": "2026-09",
                "commercial_use_confirmation_ref": "ticket://provider/7781",
            },
        }
        res_post = qa_client.post(
            f"/qa/quarantine/{case_id}/resolve",
            json=resolve_payload,
            headers=headers,
        )
        assert res_post.status_code == 200
        assert res_post.json()["status"] == "RESOLVED"
        assert res_post.json()["resolution"] == "CLEARED_BY_OVERRIDE"

        # 4. Manifest is available: automated disposition kept, override recorded (R8)
        man_res = qa_client.get("/qa/release-manifests/cand_quar_api_01", headers=headers)
        assert man_res.status_code == 200
        assert man_res.json()["disposition"] == "QUARANTINED"
        assert man_res.json()["override"]["status"] == "CLEARED_BY_OVERRIDE"
        assert man_res.json()["override"]["quarantine_case_id"] == case_id
        assert man_res.json()["lineage"]["content_arm_id"] == "arm_api_02"

    finally:
        if wav_path.is_file():
            wav_path.unlink()


def test_evaluation_refuses_an_incomplete_candidate_event(qa_client, qa_engine) -> None:
    headers = {SERVICE_TOKEN_HEADER: _TEST_TOKEN}
    event = candidate_event().to_wire()
    del event["lineage"]
    res = qa_client.post(
        "/qa/evaluations",
        json={"candidate_event": event},
        headers=headers,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "missing_required_field"
    assert "candidate_event.lineage" in res.json()["detail"]["field_paths"]


def test_a_concurrent_duplicate_evaluation_replays_the_stored_one(
        qa_client, qa_engine, store, monkeypatch: pytest.MonkeyPatch) -> None:
    """R5: a caller's retry while the first request was still evaluating passed the
    replay check, evaluated again and failed on the evaluation's key with a 500, which
    ended the pipeline run. Proves: when the same evaluation was committed meanwhile, the
    second request answers with the stored evaluation (same id, same disposition)."""
    import quality_gate.api.routes as routes

    wav_path = _make_temp_wav(duration=1.5)
    headers = {SERVICE_TOKEN_HEADER: _TEST_TOKEN}
    try:
        body = {"candidate_event": _stored_event(
            store, wav_path, candidate_id="cand_api_race", artifact_id="art_api_race",
            track_spec_id="spec_api_race",
            lineage=lineage_fields(experiment_id="exp_api_race", content_arm_id="arm_race"),
            prompt_text="relaxing lo-fi chill beat")}
        first = qa_client.post("/qa/evaluations", json=body, headers=headers)
        assert first.status_code == 201, first.text

        real = routes.get_candidate_evaluation
        calls = {"n": 0}

        def not_yet_visible(session, evaluation_id):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return None if calls["n"] == 1 else real(session, evaluation_id)

        monkeypatch.setattr(routes, "get_candidate_evaluation", not_yet_visible)
        second = qa_client.post("/qa/evaluations", json=body, headers=headers)
        assert second.status_code in (200, 201), second.text
        assert second.json()["evaluation_id"] == first.json()["evaluation_id"]
        assert second.json()["disposition"] == first.json()["disposition"]
        assert calls["n"] == 2
    finally:
        if wav_path.is_file():
            wav_path.unlink()

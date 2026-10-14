"""Unit tests for Phase P5 FastAPI endpoints and service-token auth (P5 §94, R7, R8, R15)."""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.exceptions import StaticReviewerDirectory
from quality_gate.api.app import create_app
from quality_gate.config import load_quality_gate_settings

from tests.support.qa_db import create_qa_engine, qa_session
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
    return create_qa_engine(static_pool=True)


@pytest.fixture
def qa_client(qa_engine):
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
    )
    return TestClient(app)


def _make_temp_wav(duration: float = 1.0) -> Path:
    temp_dir = tempfile.gettempdir()
    path = Path(temp_dir) / f"api_test_{int(duration*1000)}.wav"
    sample_rate = 44100
    total_frames = int(duration * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(total_frames):
            t = float(i) / sample_rate
            val = int(14000.0 * math.sin(2.0 * math.pi * 440.0 * t))
            frames.extend(struct.pack("<hh", val, val))
        wf.writeframes(frames)
    return path


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


def test_evaluate_and_get_manifest_flow(qa_client, qa_engine) -> None:
    wav_path = _make_temp_wav(duration=1.0)
    try:
        headers = {SERVICE_TOKEN_HEADER: _TEST_TOKEN}
        eval_payload = {
            "candidate_event": candidate_event(
                candidate_id="cand_api_01",
                artifact_id="art_api_01",
                track_spec_id="spec_api_01",
                lineage=lineage_fields(experiment_id="exp_api_01", content_arm_id="arm_api_01"),
                prompt_text="relaxing lo-fi chill beat",
            ).to_wire(),
            "audio_path": str(wav_path),
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


def test_quarantine_and_resolve_flow(qa_client, qa_engine) -> None:
    wav_path = _make_temp_wav(duration=1.0)
    try:
        headers = {SERVICE_TOKEN_HEADER: _TEST_TOKEN}
        # Declared commercial use unknown -> triggers quarantine
        eval_payload = {
            "candidate_event": candidate_event(
                candidate_id="cand_quar_api_01",
                artifact_id="art_api_02",
                track_spec_id="spec_api_02",
                lineage=lineage_fields(experiment_id="exp_api_02", content_arm_id="arm_api_02"),
                rights=rights_fields(commercial_use_status="unknown"),
            ).to_wire(),
            "audio_path": str(wav_path),
        }

        # 1. Post evaluation -> Quarantined
        post_res = qa_client.post("/qa/evaluations", json=eval_payload, headers=headers)
        assert post_res.status_code == 201
        data = post_res.json()
        assert data["disposition"] == "QUARANTINED"
        case_id = data["quarantine_case_id"]
        assert case_id is not None

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
        json={"candidate_event": event, "audio_path": "unused.wav"},
        headers=headers,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "missing_required_field"
    assert "candidate_event.lineage" in res.json()["detail"]["field_paths"]

"""Gate R4 storage evidence: P4 stores audio on real MinIO and P5 evaluates it by URI.

Against the compose stack: MinIO (``MINIO_ENDPOINT``), and the ``api-music`` (8004) and
``api-qa`` (8005) containers, which run this checkout bind-mounted at ``/app``
(``PLM_SERVICE_URL__MUSIC`` / ``__QA`` override; restart them after code changes with
``docker compose restart api-music api-qa``). Nothing on either side is stubbed: P4
generates with the mock provider and writes to MinIO, P4's export builds the C4 event,
and P5 reads the bytes by the event's URI and verifies them against its sha256.

Every run uses fresh track-spec and candidate ids. The MinIO objects a test writes are
removed afterwards (best effort); the ``music`` and ``qa`` rows of the run are kept,
like other append-only evidence. Skips with the reason when the token is unset or a
service is unreachable.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from minio import Minio
from minio.error import S3Error
from plm_contracts.seams.c3 import TrackSpecPayload
from plm_platform.config import SERVICE_TOKEN_HEADER, Settings, SettingsError, load_settings
from plm_platform.pipeline.http import HttpPhaseSteps, PhaseClient, PhaseRequestRefused
from plm_platform.pipeline.states import Step
from plm_platform.pipeline.types import StepRequest
from plm_platform.service_urls import LOOPBACK_HOST, service_url
from plm_platform.storage import (
    build_client,
    build_key,
    ensure_bucket,
    from_uri,
    get_verified,
    sha256_digest,
    to_uri,
)
from plm_platform.temporal import Phase

from services.music_factory.storage.artifacts import MinioArtifactStorage
from services.quality_gate.ingestion.artifact_fetch import (
    ArtifactFetchRefused,
    MinioArtifactFetcher,
)
from tests.support.seams import lineage_fields, track_spec_fields

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSING = {"PASSED", "PASSED_WITH_WARNINGS"}
"""The gate's verdict on mock audio is not what these tests prove (it depends on the P5
detectors configured); they prove that whatever P5 decides, it decided on the bytes it
read by URI, and that each disposition carries the facts it implies."""


@dataclass
class Stack:
    settings: Settings
    minio: Minio
    token: str
    music: httpx.Client
    qa: httpx.Client
    keys: list[str] = field(default_factory=list)

    @property
    def headers(self) -> dict[str, str]:
        return {SERVICE_TOKEN_HEADER: self.token}


@pytest.fixture(scope="module")
def settings() -> Settings:
    try:
        return load_settings(dotenv_path=REPO_ROOT / ".env")
    except SettingsError as exc:
        pytest.skip(f"no usable configuration: {exc} - copy .env.example to .env")


@pytest.fixture(scope="module")
def minio(settings: Settings) -> Minio:
    client = build_client(settings)
    try:
        ensure_bucket(settings=settings, client=client)
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        pytest.skip(f"minio not reachable at {settings.minio_endpoint} - {exc}; "
                    "start it with: docker compose up -d --wait minio")
    return client


def _api(phase: Phase, token: str) -> httpx.Client:
    base_url = service_url(phase, dotenv_path=REPO_ROOT / ".env", default_host=LOOPBACK_HOST)
    client = httpx.Client(base_url=base_url, timeout=60.0)
    try:
        client.get("/readyz")
    except httpx.HTTPError as exc:
        pytest.skip(f"{phase.name} API not reachable at {base_url} - {exc}; "
                    "start it with: docker compose up -d --wait")
    probe = client.get("/qa/quarantine" if phase is Phase.QA else "/music/providers",
                       headers={SERVICE_TOKEN_HEADER: token})
    assert probe.status_code == 200, (
        f"{phase.name} refused the service token ({probe.status_code}: {probe.text}); "
        "recreate it with PLM_SERVICE_TOKEN exported")
    return client


@pytest.fixture
def stack(settings: Settings, minio: Minio) -> Iterator[Stack]:
    if settings.service_token is None:
        pytest.skip("PLM_SERVICE_TOKEN is unset: export the dev token (.env.example)")
    token = settings.service_token.get_secret_value()
    music = _api(Phase.MUSIC, token)
    qa = _api(Phase.QA, token)
    state = Stack(settings=settings, minio=minio, token=token, music=music, qa=qa)
    try:
        yield state
    finally:
        music.close()
        qa.close()
    for key in state.keys:
        try:
            minio.remove_object(settings.minio_bucket, key)
        except S3Error:  # cleanup is best-effort
            pass


def _spec(run: str) -> dict[str, Any]:
    return TrackSpecPayload.build(**track_spec_fields(
        track_spec_id=f"trk_r4_{run}",
        lineage=lineage_fields(experiment_id=f"exp_r4_{run}", content_arm_id=f"arm_r4_{run}"),
        requested_duration=20,
        duration_range={"min_seconds": 10, "max_seconds": 40},
        candidate_target=1,
        bounded_preferences={"bpm": 88, "key_profile": None, "mood": ["melancholic"],
                             "use_case": ["late_night_drive"]},
    )).to_wire()


def _generate(stack: Stack) -> str:
    """Plan and execute one fresh spec on the running P4; returns the candidate id."""
    spec = _spec(uuid.uuid4().hex[:12])
    plan = stack.music.post("/music/plans", json=spec, headers=stack.headers)
    assert plan.status_code == 200, plan.text
    run = stack.music.post(f"/music/plans/{plan.json()['plan_id']}/execute", json=spec,
                           headers=stack.headers)
    assert run.status_code == 200, run.text
    candidates = run.json()["candidates"]
    assert len(candidates) == 1, run.text
    return str(candidates[0]["candidate_id"])


def _export(stack: Stack, candidate_id: str) -> dict[str, Any]:
    res = stack.music.post(f"/music/candidates/{candidate_id}/export-to-p5",
                           headers=stack.headers)
    assert res.status_code == 200, res.text
    event: dict[str, Any] = res.json()["event"]
    stack.keys.append(from_uri(event["artifact_uri"], settings=stack.settings))
    return event


def _evaluate(stack: Stack, event: dict[str, Any]) -> httpx.Response:
    return stack.qa.post("/qa/evaluations", json={"candidate_event": event},
                         headers=stack.headers)


def test_real_minio_round_trip_through_the_p4_store_and_the_p5_fetcher(
    settings: Settings, minio: Minio
) -> None:
    """Proves on real MinIO: P4's store writes content-addressed bytes and returns a
    plm:// URI; P5's fetcher reads them back verified; tampered bytes and a missing
    object are refused (artifact_integrity_failed, artifact_not_found)."""
    run = uuid.uuid4().hex[:12]
    audio = b"RIFF" + uuid.uuid4().bytes * 64
    storage = MinioArtifactStorage(settings)
    fetcher = MinioArtifactFetcher(settings)
    uri = storage.put_artifact(candidate_id=f"cand_r4_{run}", name="master.wav", data=audio)
    key = from_uri(uri, settings=settings)
    tampered_key = build_key("music/", f"cand_r4_{run}/tampered.wav", sha256_digest(audio))
    try:
        assert uri.startswith(f"plm://{settings.minio_bucket}/music/cand_r4_{run}/")
        assert storage.get_artifact(uri) == audio
        assert fetcher.fetch(uri, sha256_digest(audio)) == audio

        # Bytes written under a key whose digest they do not match (tampering).
        forged = audio + b"!"
        minio.put_object(settings.minio_bucket, tampered_key, io.BytesIO(forged), len(forged))
        tampered_uri = to_uri(tampered_key, settings=settings)
        with pytest.raises(ArtifactFetchRefused) as tampered:
            fetcher.fetch(tampered_uri, sha256_digest(audio))
        assert tampered.value.error.code == "artifact_integrity_failed"

        missing = to_uri(build_key("music/", f"cand_r4_{run}/missing.wav",
                                   sha256_digest(b"absent")), settings=settings)
        with pytest.raises(ArtifactFetchRefused) as absent:
            fetcher.fetch(missing, sha256_digest(b"absent"))
        assert absent.value.error.code == "artifact_not_found"
    finally:
        for k in (key, tampered_key):
            try:
                minio.remove_object(settings.minio_bucket, k)
            except S3Error:
                pass


def test_p4_api_stores_to_minio_and_p5_evaluates_by_uri(stack: Stack) -> None:
    """Proves on the running stack: the P4 API (mock provider) writes the audio to real
    MinIO and exports a C4 event naming it by plm:// URI and sha256; the bytes in MinIO
    hash to that sha256; P5's API evaluates the candidate from the event alone (no path)
    and records an evaluation of that artifact: a pass carries a release manifest naming
    the same URI and sha256, a quarantine carries its reason codes."""
    candidate_id = _generate(stack)
    event = _export(stack, candidate_id)
    assert event["artifact_uri"].startswith(f"plm://{stack.settings.minio_bucket}/music/")
    key = from_uri(event["artifact_uri"], settings=stack.settings)
    stored = get_verified(key, settings=stack.settings, client=stack.minio)
    assert sha256_digest(stored) == event["artifact_sha256"]

    res = _evaluate(stack, event)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["candidate_id"] == candidate_id
    stored_eval = stack.qa.get(f"/qa/evaluations/{body['evaluation_id']}",
                               headers=stack.headers)
    assert stored_eval.status_code == 200, stored_eval.text
    assert stored_eval.json()["disposition"] == body["disposition"]
    manifest = stack.qa.get(f"/qa/release-manifests/{candidate_id}", headers=stack.headers)
    if body["disposition"] in PASSING:
        assert manifest.status_code == 200, manifest.text
        assert manifest.json()["artifact_uri"] == event["artifact_uri"]
        assert manifest.json()["artifact_sha256"] == event["artifact_sha256"]
    else:
        assert manifest.status_code == 404  # no manifest for an uncleared candidate
        if body["disposition"] == "QUARANTINED":
            assert body["quarantine_case_id"] and body["quarantine_reason_codes"]


def test_p5_api_refuses_tampered_and_missing_artifacts(stack: Stack) -> None:
    """Proves on the running stack: an event whose object in MinIO was tampered with is
    refused 409 artifact_integrity_failed, one whose object does not exist is refused
    409 artifact_not_found, and neither produces an evaluation or a manifest."""
    candidate_id = _generate(stack)
    event = _export(stack, candidate_id)
    original = get_verified(from_uri(event["artifact_uri"], settings=stack.settings),
                            settings=stack.settings, client=stack.minio)
    run = uuid.uuid4().hex[:12]

    tampered_id = f"cand_r4_tampered_{run}"
    tampered_key = build_key("music/", f"{tampered_id}/master.wav", event["artifact_sha256"])
    forged = original[:-4] + b"\x00\x00\x00\x01"
    stack.minio.put_object(stack.settings.minio_bucket, tampered_key, io.BytesIO(forged),
                           len(forged))
    stack.keys.append(tampered_key)
    tampered = _evaluate(stack, {**event, "candidate_id": tampered_id,
                                 "artifact_uri": to_uri(tampered_key, settings=stack.settings)})
    assert tampered.status_code == 409, tampered.text
    assert tampered.json()["detail"]["code"] == "artifact_integrity_failed"

    missing_id = f"cand_r4_missing_{run}"
    missing_key = build_key("music/", f"{missing_id}/master.wav", event["artifact_sha256"])
    missing = _evaluate(stack, {**event, "candidate_id": missing_id,
                                "artifact_uri": to_uri(missing_key, settings=stack.settings)})
    assert missing.status_code == 409, missing.text
    assert missing.json()["detail"]["code"] == "artifact_not_found"

    for refused in (tampered_id, missing_id):
        manifest = stack.qa.get(f"/qa/release-manifests/{refused}", headers=stack.headers)
        assert manifest.status_code == 404


def _steps(stack: Stack) -> HttpPhaseSteps:
    return HttpPhaseSteps({
        Phase.MUSIC: PhaseClient(Phase.MUSIC, stack.music, token=stack.token),
        Phase.QA: PhaseClient(Phase.QA, stack.qa, token=stack.token),
    })


def _qa_request(candidate_ids: list[str]) -> StepRequest:
    run = uuid.uuid4().hex[:12]
    return StepRequest(workflow_id=f"pipeline.r4-{run}", directive_id=f"r4-{run}",
                       brand_id="gate_brand", step=Step.QA.value,
                       idempotency_key=f"pipeline.r4-{run}:qa",
                       requested_at=datetime.now(UTC),
                       inputs={"generate": {"candidate_ids": candidate_ids}})


def test_pipeline_qa_step_against_the_stack(stack: Stack) -> None:
    """Proves on the running stack: the pipeline's HTTP ``qa`` step gets each candidate's
    C4 event from P4's export, has P5 evaluate it by URI, and returns a disposition the
    workflow can act on (PASSED with the release manifest, QUARANTINED with one hold per
    reason code, or REJECTED); repeating the step (a Temporal retry) replays the same
    evaluation instead of evaluating twice."""
    candidate_id = _generate(stack)
    first = asyncio.run(_steps(stack).qa(_qa_request([candidate_id])))
    stack.keys.append(from_uri(
        _export(stack, candidate_id)["artifact_uri"], settings=stack.settings))
    evaluation = first.outputs["evaluations"][0]
    assert evaluation["candidate_id"] == candidate_id
    if evaluation["disposition"] in PASSING:
        assert first.outputs["disposition"] == "PASSED"
        assert first.outputs["passed_candidate_ids"] == [candidate_id]
        assert first.outputs["release_manifest_ids"] == [evaluation["release_manifest_id"]]
        assert first.outputs["holds"] == []
    elif evaluation["disposition"] == "QUARANTINED":
        assert first.outputs["disposition"] == "QUARANTINED"
        assert [h["reason_code"] for h in first.outputs["holds"]] == evaluation["reason_codes"]
        assert evaluation["reason_codes"]
        assert first.outputs["passed_candidate_ids"] == []
    else:
        assert first.outputs["disposition"] == "REJECTED"

    again = asyncio.run(_steps(stack).qa(_qa_request([candidate_id])))
    assert again.outputs == first.outputs


def test_pipeline_qa_step_fails_closed_on_an_unknown_candidate(stack: Stack) -> None:
    """Proves on the running stack: a candidate P4 cannot export ends the step with a
    definite refusal; no disposition is produced."""
    with pytest.raises(PhaseRequestRefused) as refused:
        asyncio.run(_steps(stack).qa(_qa_request([f"cand_r4_unknown_{uuid.uuid4().hex}"])))
    assert refused.value.code == "candidate_not_found"

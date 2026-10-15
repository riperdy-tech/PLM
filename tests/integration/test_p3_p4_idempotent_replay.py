"""R5 evidence: P3 plans supply to the declared range, and P3 -> P4 retries replay by key.

Against the compose stack: Postgres (migrations through ``0035_idempotency_records``),
MinIO, and the ``api-experiments`` (8083) and ``api-music`` (8004) containers running
this checkout (restart them after code changes with ``PLM_SERVICE_TOKEN`` exported:
``docker compose up -d --wait --force-recreate api-experiments api-music``). Nothing is
stubbed: P3 designs, validates and materializes from a C2 directive, P4 plans, generates
with the mock provider into MinIO and exports the C4 event.

Every request carries an ``Idempotency-Key`` and is sent twice, as a caller does when a
response is lost. Proves, over HTTP and on Postgres: the replay returns the first answer
(where the retry used to fail closed on its own duplicate insert); the same key with a
different request is refused with 409 ``idempotency_key_reused``; the package declares
the 45–75 min range of its ``45_75m`` directive with enough specs plus spares; and an
extension replays its specs by key. It does not prove the pipeline's use of these
routes (the pipeline's own integration tests) or a real provider (R6).

Every run uses fresh directive and arm ids; MinIO objects it writes are removed
afterwards (best effort); database rows are kept as evidence.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from minio.error import S3Error
from plm_contracts.seams.c2 import ProductionDirectivePayload
from plm_platform.config import SERVICE_TOKEN_HEADER, Settings, SettingsError, load_settings
from plm_platform.service_urls import LOOPBACK_HOST, service_url
from plm_platform.storage import build_client, from_uri
from plm_platform.temporal import Phase

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Stack:
    settings: Settings
    token: str
    p3: httpx.Client
    p4: httpx.Client
    keys: list[str] = field(default_factory=list)

    def post(self, client: httpx.Client, path: str, key: str,
             body: Any = None) -> httpx.Response:
        return client.post(path, json=body, headers={
            SERVICE_TOKEN_HEADER: self.token, "Idempotency-Key": key})

    def twice(self, client: httpx.Client, path: str, key: str,
              body: Any = None) -> dict[str, Any]:
        """Send a request, then retry it as if the first response had been lost."""
        first = self.post(client, path, key, body)
        assert first.status_code == 200, first.text
        retry = self.post(client, path, key, body)
        assert retry.status_code == 200, retry.text
        assert retry.json() == first.json()
        answer: dict[str, Any] = first.json()
        return answer


def _api(phase: Phase, token: str, probe: str) -> httpx.Client:
    base_url = service_url(phase, dotenv_path=REPO_ROOT / ".env", default_host=LOOPBACK_HOST)
    client = httpx.Client(base_url=base_url, timeout=120.0)
    try:
        client.get("/readyz")
    except httpx.HTTPError as exc:
        pytest.skip(f"{phase.name} API not reachable at {base_url} - {exc}; "
                    "start it with: docker compose up -d --wait")
    answer = client.get(probe, headers={SERVICE_TOKEN_HEADER: token})
    assert answer.status_code == 200, (
        f"{phase.name} refused the service token ({answer.status_code}: {answer.text}); "
        "recreate it with PLM_SERVICE_TOKEN exported")
    return client


@pytest.fixture
def stack() -> Iterator[Stack]:
    try:
        settings = load_settings(dotenv_path=REPO_ROOT / ".env")
    except SettingsError as exc:
        pytest.skip(f"no usable configuration: {exc} - copy .env.example to .env")
    if settings.service_token is None:
        pytest.skip("PLM_SERVICE_TOKEN is unset: export the dev token (.env.example)")
    token = settings.service_token.get_secret_value()
    state = Stack(settings=settings, token=token,
                  p3=_api(Phase.EXPERIMENTS, token, "/experiments/v1/experiments?limit=1"),
                  p4=_api(Phase.MUSIC, token, "/music/providers"))
    try:
        yield state
    finally:
        state.p3.close()
        state.p4.close()
        if state.keys:
            minio = build_client(settings)
            for key in state.keys:
                try:
                    minio.remove_object(settings.minio_bucket, key)
                except S3Error:  # cleanup is best-effort
                    pass


def _directive(run: str) -> dict[str, Any]:
    return ProductionDirectivePayload.build(
        directive_id=f"dir_r5_{run}", decision_id=f"dec_r5_{run}",
        arm_id=f"rnb_late_night_drive_english_vocal_45_75m_{run}",
        allocation_type="exploit", priority=1, quantity=1,
        brief={"genre": "rnb", "mood": ["melancholic"], "use_case": ["late_night_drive"],
               "language": "english", "vocal_mode": "vocal", "duration_bucket": "45_75m",
               "visual_world": "rainy_asian_city"},
        expected_value=1.0, uncertainty=0.2, budget_limit=100.0,
        market_context={"brand_id": "neon_seoul"}, selection_reason=["r5_integration"],
        directive_version="p2-dir-v1", policy_version="p2-v1",
        expires_at="2027-01-01T00:00:00Z", exploration_fraction=0.2,
        value_model="early-stage-proxy-v1", outcome_horizon="28d",
    ).to_wire()


def test_p3_to_p4_retries_replay_and_reused_keys_are_refused(stack: Stack) -> None:
    run = uuid.uuid4().hex[:12]

    def key(name: str) -> str:
        return f"r5-it-{run}-{name}"

    # P3: design, validate and materialize, each retried after a "lost" response.
    designed = stack.twice(stack.p3, "/experiments/v1/experiments", key("design"),
                           {"directive": _directive(run), "seed": 1})
    experiment = designed["experiment"]
    exp_id = experiment["experiment_id"]
    reused = stack.post(stack.p3, "/experiments/v1/experiments", key("design"),
                        {"directive": _directive(run), "seed": 2})
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "idempotency_key_reused"

    validated = stack.twice(stack.p3, f"/experiments/v1/experiments/{exp_id}/validate",
                            key("validate"))
    assert validated["status"] == "validated"

    package = stack.twice(
        stack.p3, f"/experiments/v1/experiments/{exp_id}/materialize", key("materialize"),
        {"treatment_id": experiment["treatments"][0]["treatment_id"]})["package"]
    playlist = package["playlist_spec"]
    assert playlist["duration_range_seconds"] == {"min_seconds": 2700, "max_seconds": 4500}
    assert playlist["target_duration_seconds"] == 3600
    planned = playlist["planned_track_duration_seconds"]
    assert playlist["base_track_count"] * planned >= 2700
    assert len(package["track_specs"]) == playlist["track_count_target"] == (
        playlist["base_track_count"] + playlist["spare_track_count"])

    fetched = stack.p3.get(f"/experiments/v1/packages/{package['package_id']}",
                           headers={SERVICE_TOKEN_HEADER: stack.token})
    assert fetched.status_code == 200
    assert fetched.json()["playlist_spec"] == playlist

    extended = stack.twice(
        stack.p3, f"/experiments/v1/packages/{package['package_id']}/extend", key("extend"),
        {"count": 2, "idempotency_key": key("extend-body")})
    assert len(extended["track_specs"]) == 2
    # The body's key alone (a new header key, or none) also returns the same top-up.
    again = stack.post(stack.p3, f"/experiments/v1/packages/{package['package_id']}/extend",
                       key("extend-2"), {"count": 2, "idempotency_key": key("extend-body")})
    assert again.status_code == 200
    assert [s["track_spec_id"] for s in again.json()["track_specs"]] == \
        [s["track_spec_id"] for s in extended["track_specs"]]

    # P4: plan, execute and export one of the package's specs, each retried.
    spec = package["track_specs"][0]
    plan = stack.twice(stack.p4, "/music/plans", key("plan"), spec)
    other = stack.post(stack.p4, "/music/plans", key("plan"), package["track_specs"][1])
    assert other.status_code == 409
    assert other.json()["detail"]["code"] == "idempotency_key_reused"

    executed = stack.twice(stack.p4, f"/music/plans/{plan['plan_id']}/execute",
                           key("execute"), spec)
    assert executed["status"] == "completed"
    assert {(w["capability"], w["decision"]) for w in executed["external_writes"]} == {
        ("music.generate", "simulate")}
    candidate_id = executed["candidates"][0]["candidate_id"]
    attempts = stack.p4.get(f"/music/candidates/{candidate_id}/attempts",
                            headers={SERVICE_TOKEN_HEADER: stack.token}).json()
    assert len(attempts) == executed["attempts_count"]  # the retry generated nothing

    exported = stack.twice(stack.p4, f"/music/candidates/{candidate_id}/export-to-p5",
                           key("export"))
    event = exported["event"]
    assert event["requested_duration_range"] == spec["duration_range"]
    assert event["lineage"] == spec["lineage"]
    for candidate in executed["candidates"]:
        artifacts = stack.p4.get(f"/music/candidates/{candidate['candidate_id']}/artifacts",
                                 headers={SERVICE_TOKEN_HEADER: stack.token}).json()
        stack.keys.extend(from_uri(a["object_uri"], settings=stack.settings)
                          for a in artifacts)

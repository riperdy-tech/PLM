"""R5: the publishing, measurement and reward steps over HTTP against the running stack.

Each step is called on its own as the pipeline worker calls it (``HttpPhaseSteps`` with
the development configuration ``config/dev/pipeline.json``), against the compose
containers: ``api-youtube`` (P7, mock YouTube client and mock analytics source),
``api-distribution`` (P8, mock distributor and mock statements), ``api-analytics`` (P9),
``api-qa`` (P5), ``api-media`` (P6) and ``api-ar`` (P2). The workflow is not involved;
the full chain under Temporal is the separate end-to-end test.

Setup this test owns, standing in for what it does not prove:

* P4 and P5 (as in ``tests/integration/test_media_assemble_http.py``): synthetic track
  audio stored in MinIO and P5 release manifests inserted into P5's table; P6 then
  assembles them for real (preview render profile);
* the operator: P7's channel profile for the configured channel;
* P1/P2: an arm-state row for a fresh arm (P2 refuses results for arms it does not
  know) and the P2 allocation decision in the decision log (P9 attaches the outcome
  to it).

Afterwards the test removes what it wrote to P2 and P9 for its fresh ids (P9's statement
lines carry no track id, which migration 0020's downgrade cannot hold; see the
migration round-trip test). P5/P6/P7/P8 rows stay, like other append-only evidence.

Proves: P7 publishes the C6 package and replays it idempotently with its write receipts;
P8 submits the release from P5's manifests, the package and the publication event and
returns its ISRCs and C7 record; P7 reports the publication processed; the ledger read
turns simulated writes into recorded zeros; P9 measures the unit from the mock sources it
ingested (labelled ``synthetic``), with no amount from the pipeline, and its outbox
delivers the reward to the running P2. The learning step reaches P2 (a second test): it
applies the reward and runs the next cycle when P2 has an active policy and can reach
P1; otherwise P2's named refusal is accepted or reported, never faked.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from plm_contracts import GateDisposition
from plm_platform.config import SERVICE_TOKEN_HEADER, Settings
from plm_platform.exceptions import ExceptionQueue
from plm_platform.ledger import BudgetLedger
from plm_platform.pipeline.activities import PlatformOperations
from plm_platform.pipeline.config import load_pipeline_config
from plm_platform.pipeline.http import (
    HttpPhaseSteps,
    PhaseClient,
    PhaseRequestFailed,
    PhaseRequestRefused,
)
from plm_platform.pipeline.states import Step
from plm_platform.pipeline.types import StepRequest, WriteCostsRequest
from plm_platform.service_urls import LOOPBACK_HOST, service_urls
from plm_platform.storage import MUSIC_PREFIX, build_client, put_content, to_uri
from plm_platform.temporal import Phase
from sqlalchemy.orm import Session, sessionmaker

from services.analytics.storage.models import (
    OUTBOX_EVENTS,
    CostItemRow,
    ExperimentOutcomeRow,
    ReleaseLinkRow,
    RevenueStatementRow,
    RewardPostedRow,
    TrackMetricRow,
    VideoMetricRow,
)
from services.analytics.storage.repositories import get_costs_for_entity, get_video_metrics
from services.autonomous_ar.storage.models import (
    ar_content_arm_state,
    ar_experiment_result,
    ar_reward_observation,
)
from services.autonomous_ar.storage.repositories import upsert_arm_state
from services.quality_gate.gate.manifest import build_release_manifest
from services.quality_gate.storage.repositories import insert_release_manifest
from services.youtube_publishing.domain.models import ChannelProfile
from services.youtube_publishing.storage.repositories import (
    get_channel_profile_by_channel_id,
    insert_channel_profile,
)
from tests.integration.pipeline.support import REPO_ROOT
from tests.support.decisions import log_allocation_decision
from tests.support.media_audio import synth_track
from tests.support.seams import sample_lineage

pytestmark = pytest.mark.integration

BRAND = "neon_seoul"
TRACKS = ((12.0, {"bpm": 120.0, "tonic": 0}), (13.0, {"bpm": 122.0, "tonic": 7}),
          (12.0, {"bpm": 118.0, "tonic": 9, "minor": True}))
CONFIG = load_pipeline_config(env={"PLM_PIPELINE_CONFIG_FILE": str(
    REPO_ROOT / "config" / "dev" / "pipeline.json")}, dotenv_path=REPO_ROOT / "none.env")


@dataclass
class Run:
    run: str
    workflow_id: str
    lineage: Any
    media_package_id: str
    steps: HttpPhaseSteps
    factory: sessionmaker[Session]
    videos: list[str] = field(default_factory=list)
    releases: list[str] = field(default_factory=list)
    isrcs: list[str] = field(default_factory=list)
    matured_at: datetime | None = None


def _request(run: Run, step: Step, inputs: dict[str, Any], at: datetime) -> StepRequest:
    return StepRequest(workflow_id=run.workflow_id, directive_id=f"dir_r5_{run.run}",
                       brand_id=BRAND, step=step.value,
                       idempotency_key=f"{run.workflow_id}:{step.value}", requested_at=at,
                       inputs=inputs)


def _steps(token: str) -> HttpPhaseSteps:
    urls = service_urls(dotenv_path=REPO_ROOT / ".env", default_host=LOOPBACK_HOST)
    return HttpPhaseSteps({
        phase: PhaseClient(phase, httpx.Client(base_url=url, timeout=300.0), token=token)
        for phase, url in urls.items()}, config=CONFIG)


def _ready(token: str) -> None:
    urls = service_urls(dotenv_path=REPO_ROOT / ".env", default_host=LOOPBACK_HOST)
    for phase in (Phase.AR, Phase.QA, Phase.MEDIA, Phase.YOUTUBE, Phase.DISTRIBUTION,
                  Phase.ANALYTICS):
        try:
            ready = httpx.get(f"{urls[phase]}/readyz", timeout=10.0)
        except httpx.HTTPError as exc:
            pytest.skip(f"{phase.name} API not reachable: {exc}")
        if ready.status_code != 200:
            pytest.skip(f"{phase.name} API not ready: {ready.status_code}")


def _stage_and_assemble(settings: Settings, factory: sessionmaker[Session], token: str,
                        run: str, lineage: Any) -> str:
    """P4/P5 stand-in, then the real P6: returns the media package id."""
    candidates = []
    with factory() as session:
        for n, (seconds, synth) in enumerate(TRACKS):
            candidate_id = f"cand_r5_{run}_{n}"
            stored = put_content(MUSIC_PREFIX, f"it_r5/{candidate_id}/full_mix.wav",
                                 synth_track(seconds=seconds, **synth),  # type: ignore[arg-type]
                                 content_type="audio/wav", settings=settings,
                                 client=build_client(settings))
            insert_release_manifest(session, build_release_manifest(
                candidate_id=candidate_id, canonical_artifact_id=f"art_r5_{run}_{n}",
                artifact_uri=to_uri(stored.key, settings=settings),
                artifact_sha256=stored.digest, measured_duration_seconds=seconds,
                track_spec_id=f"trk_r5_{run}_{n}", lineage=lineage,
                disposition=GateDisposition.PASSED, override=None, technical_status="PASSED",
                commercial_use_status="eligible", provenance_status="COMPLETE",
                ai_disclosure_required=True, rights_risk="LOW", policy_status="COMPLIANT",
                evaluation_id_str=f"eval_r5_{run}_{n}", evaluation_policy_version="p5-v1",
                warning_codes=[], evidence_bundle_id=f"evi_r5_{run}_{n}",
                now=datetime(2026, 10, 15, tzinfo=UTC)))
            candidates.append(candidate_id)
        session.commit()
    urls = service_urls(dotenv_path=REPO_ROOT / ".env", default_host=LOOPBACK_HOST)
    total = int(sum(t[0] for t in TRACKS) - 4.0)
    answer = httpx.post(f"{urls[Phase.MEDIA]}/media/packages/assemble", timeout=300.0,
                        headers={SERVICE_TOKEN_HEADER: token}, json={
                            "candidate_ids": candidates, "brand_id": BRAND,
                            "duration_range_seconds": {"min_seconds": total,
                                                       "max_seconds": total + 1},
                            "visual_profile": "it_waveform",
                            "playlist_concept": "R5 Integration Focus",
                            "render_profile": "preview_360p_v1"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "ready_for_publish", answer.json()
    return str(answer.json()["media_package_id"])


@pytest.fixture(scope="module")
def stack(settings: Settings, factory: sessionmaker[Session]) -> Iterator[Run]:
    if settings.service_token is None:
        pytest.skip("PLM_SERVICE_TOKEN is not set")
    token = settings.service_token.get_secret_value()
    _ready(token)
    if CONFIG.publish is None:
        pytest.skip("config/dev/pipeline.json has no publish section")
    run = uuid.uuid4().hex[:10]
    lineage = sample_lineage(experiment_id=f"exp_r5_{run}", content_arm_id=f"arm_r5_{run}",
                             production_directive_id=f"dir_r5_{run}",
                             experiment_family_id=f"fam_r5_{run}",
                             content_genome_id=f"gen_r5_{run}")
    now = datetime.now(UTC)
    with factory() as session:
        upsert_arm_state(session, brand_id=BRAND, arm_id=lineage.content_arm_id, now=now)
        if get_channel_profile_by_channel_id(session, CONFIG.publish.channel_id) is None:
            insert_channel_profile(session, ChannelProfile(
                id=f"prof_{CONFIG.publish.channel_id}", channel_id=CONFIG.publish.channel_id,
                channel_name="PLM development mock channel", brand_id=BRAND,
                oauth_credential_ref="dev_mock_no_oauth", created_at=now, updated_at=now))
        session.commit()
    log_allocation_decision(factory.kw["bind"], directive_id=lineage.production_directive_id,
                            arm_id=lineage.content_arm_id, decided_at=now)
    media_package_id = _stage_and_assemble(settings, factory, token, run, lineage)
    created = Run(run=run, workflow_id=f"pipeline.r5-it-{run}", lineage=lineage,
                  media_package_id=media_package_id, steps=_steps(token), factory=factory)
    try:
        yield created
    finally:
        with factory() as session:
            session.execute(sa.delete(TrackMetricRow).where(
                TrackMetricRow.isrc.in_(created.isrcs)))
            for release_id in created.releases:
                session.execute(sa.delete(RevenueStatementRow).where(
                    RevenueStatementRow.source_reference.like(f"%:{release_id}:%")))
                session.execute(sa.delete(ReleaseLinkRow).where(
                    ReleaseLinkRow.release_id == release_id))
            session.execute(sa.delete(VideoMetricRow).where(
                VideoMetricRow.video_id.in_(created.videos)))
            session.execute(sa.delete(CostItemRow).where(
                CostItemRow.entity_id.in_(created.videos)))
            session.execute(sa.delete(ExperimentOutcomeRow).where(
                ExperimentOutcomeRow.experiment_id == lineage.experiment_id))
            session.execute(sa.delete(RewardPostedRow).where(
                RewardPostedRow.experiment_id == lineage.experiment_id))
            session.execute(OUTBOX_EVENTS.delete().where(
                OUTBOX_EVENTS.c.idempotency_key.like(f"%{lineage.experiment_id}%")))
            session.execute(ar_reward_observation.delete().where(
                ar_reward_observation.c.experiment_id == lineage.experiment_id))
            session.execute(ar_experiment_result.delete().where(
                ar_experiment_result.c.experiment_id == lineage.experiment_id))
            session.execute(ar_content_arm_state.delete().where(
                ar_content_arm_state.c.arm_id == lineage.content_arm_id))
            session.commit()


def test_publish_distribute_measure_and_reward_step_by_step(stack: Run) -> None:
    started = datetime.now(UTC)
    assembled = {"packages": [{"media_package_id": stack.media_package_id,
                               "p3_package_id": f"p3_r5_{stack.run}"}]}

    uploaded = asyncio.run(stack.steps.upload(
        _request(stack, Step.UPLOAD, {"assemble": assembled}, started)))
    (publication,) = uploaded.outputs["publications"]
    stack.videos.append(publication["youtube_video_id"])
    assert publication["publication_event"]["lineage"]["content_arm_id"] == \
        stack.lineage.content_arm_id
    receipts = uploaded.outputs["external_writes"]
    assert {(r["capability"], r["decision"]) for r in receipts} == {
        ("youtube.upload", "simulate"), ("youtube.thumbnail", "simulate")}
    replay = asyncio.run(stack.steps.upload(
        _request(stack, Step.UPLOAD, {"assemble": assembled}, started)))
    assert replay.outputs["publications"][0]["youtube_video_id"] == \
        publication["youtube_video_id"]
    assert {r["reason"] for r in replay.outputs["external_writes"]} == {"performed_earlier"}

    submitted = asyncio.run(stack.steps.submit_distribution(
        _request(stack, Step.SUBMIT_DISTRIBUTION, {"upload": uploaded.outputs}, started)))
    (release,) = submitted.outputs["releases"]
    stack.releases.append(release["release_id"])
    stack.isrcs.extend(release["isrcs"])
    assert len(release["isrcs"]) == len(TRACKS)
    assert release["distribution_record"]["lineage"] == stack.lineage.model_dump(mode="json")
    assert [r["decision"] for r in submitted.outputs["external_writes"]] == ["simulate"]

    processed = asyncio.run(stack.steps.await_processing(
        _request(stack, Step.AWAIT_PROCESSING, {"upload": uploaded.outputs}, started)))
    published_at = datetime.fromisoformat(processed.outputs["published_at"])

    platform = PlatformOperations(stack.factory, queue=ExceptionQueue(),
                                  ledger=BudgetLedger(stack.factory, currency="USD"))
    costs = asyncio.run(platform.write_costs(WriteCostsRequest(
        receipts=tuple(receipts + submitted.outputs["external_writes"]),
        requested_at=started)))
    assert {c.status for c in costs} == {"simulated"}

    # The horizon has elapsed: the pipeline measures at workflow.now() = published + 28d.
    matured = published_at + timedelta(days=28, minutes=1)
    cost_inputs = [{"capability": c.capability, "idempotency_key": c.idempotency_key,
                    "decision": c.decision, "status": c.status,
                    "actual": None if c.actual is None else {"amount": c.actual.amount,
                                                             "currency": c.actual.currency},
                    "basis": c.basis, "settled_at": None, "context": dict(c.context)}
                   for c in costs]
    evaluated = asyncio.run(stack.steps.evaluate_outcome(_request(
        stack, Step.EVALUATE_OUTCOME,
        {"outcome_horizon": "28d", "upload": uploaded.outputs,
         "submit_distribution": submitted.outputs, "costs": cost_inputs}, matured)))
    (reward,) = evaluated.outputs["rewards"]
    assert (reward["content_id"], reward["status"]) == (publication["youtube_video_id"],
                                                        "mature")
    assert reward["arm_id"] == stack.lineage.content_arm_id
    assert reward["measurement_source"] == "synthetic"
    with stack.factory() as session:
        snapshots = get_video_metrics(session, publication["youtube_video_id"])
        unit_costs = get_costs_for_entity(session, publication["youtube_video_id"])
    assert [s.source.value for s in snapshots] == ["synthetic"]
    assert snapshots[0].views is not None and snapshots[0].views > 0
    assert unit_costs and all(c.amount is not None and c.amount.amount == 0
                              for c in unit_costs)

    dispatched = asyncio.run(stack.steps.dispatch_reward(_request(
        stack, Step.DISPATCH_REWARD, {"evaluate_outcome": evaluated.outputs}, matured)))
    assert dispatched.outputs["delivery_status"] == "DELIVERED"
    with stack.factory() as session:
        stored = session.execute(sa.select(sa.func.count()).select_from(
            ar_reward_observation).where(
            ar_reward_observation.c.experiment_id == stack.lineage.experiment_id)).scalar_one()
    assert stored == 1

    stack.matured_at = matured


def test_learning_applies_the_reward_then_asks_p2_to_allocate_again(stack: Run) -> None:
    """Proves the learning step's HTTP contract with the running P2: ``/ar/update`` at
    the run's instant, then ``/ar/cycles``. P2's own state decides the answer: with no
    active policy P2 refuses (``policy_not_configured``); a cycle needs P1's API, which is
    reported (xfail) while it answers ``503`` (see the report: api-market serves with no
    platform settings, so it never accepts a token)."""
    if stack.matured_at is None:
        pytest.skip("the publish/measure test did not reach the reward")
    try:
        learned = asyncio.run(stack.steps.learn(_request(
            stack, Step.LEARN, {"dispatch_reward": {"delivery_status": "DELIVERED"}},
            stack.matured_at)))
    except PhaseRequestRefused as exc:
        assert exc.phase is Phase.AR and exc.code == "policy_not_configured", exc
        return
    except PhaseRequestFailed as exc:
        if exc.phase is Phase.AR and exc.code == "market_unavailable":
            pytest.xfail(f"P2 cannot reach P1's API for the next cycle: {exc}")
        raise
    assert learned.outputs["update"]["applied"] >= 0
    assert learned.provider_ids["next_cycle_id"]
    assert learned.outputs["as_of"] == stack.matured_at.isoformat()

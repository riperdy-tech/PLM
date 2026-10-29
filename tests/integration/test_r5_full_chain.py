"""Gate R5: the full chain on the compose stack, from a P1 observation to LEARNING.

Nothing between phases is built by this test. Every hand-off is a phase API call made by
the production pipeline code (:class:`~plm_platform.pipeline.http.HttpPhaseSteps` under
:class:`~plm_platform.pipeline.workflow.PipelineWorkflow`) against the running
containers.

**Operator acts (setup; the only writes this test makes outside the chain)**

* P2 policy: ``DEFAULT_POLICY`` (``p2-v1``, ``outcome_horizon = 28d``) is stored and
  activated through P2's repository (``insert_policy`` / ``activate_policy``). P2 has no
  operator route for policies; the previously active version is restored afterwards.
* P7 channel: ``PUT /youtube/channels/{channel_id}`` (the operator route added in R5) for
  the channel the development pipeline configuration names.
* P5 detector references: one unrelated recording and one unrelated lyric
  (``tests/support/rights_detectors.register_controlled_references``), registered once,
  so the reference set and corpus are configured, never UNKNOWN.
* FX: the development pipeline configuration declares the USD->KRW rate; the outcome step
  records it in P9 before it measures (nothing extra here).
* Mock outcomes: the containers' declared model (``config/dev/mock_outcomes.json``) pins
  the true effect of the two arms P1's CI corpus forms (``lofi_study_focus`` 1.4,
  ``rnb`` 0.25). The test reads the same file and calls the arm with the larger effect
  "strong", asserting the gap is clear (ratio >= ``MIN_EFFECT_RATIO``). The strong arm is
  the one P1 scores lower, so its posterior has to overturn a lower prior.
* Namespacing: P2 keys learning state (arm state, posterior, update log, results) by the
  arm id alone, and P1's CI corpus always yields the same arm ids, so the test resets
  that state for those arms before and after the run. Brand ``neon_seoul`` is the one
  brand P6's development registry declares; the cycle ids are unique (their instant).
  P1 scores written at the simulated maturity instant are removed afterwards.

**The chain**

1. P1 (``ENV=ci``, the fixture-backed connector): ``run_daily`` at T0 collects,
   classifies, forms arms and scores them; ``GET /market/v1/opportunities`` serves them.
2. P2: ``POST /ar/cycles`` (api-ar reads P1 over HTTP) produces the first cycle's
   directives (one per arm).
3. One ``PipelineWorkflow`` per directive in Temporal's **time-skipping** test server
   (``WorkflowEnvironment.start_time_skipping()``) with an in-process worker running the
   real HTTP activities: P3 design/validate/materialize, P4 generation, P5 evaluation,
   P6 assembly and render, P7 publish, P8 release, P7 processing, then ``ANALYZING``.
4. While both runs wait on their durable 28-day timers, P1's daily scoring job runs at
   the earliest maturity instant (``run_daily_step("market.opportunity_scoring")``):
   the world's P1 refresh on day 28, so the learning cycle sees current market data.
5. Awaiting the results lets the test server skip the timer. ``evaluate_outcome``
   (P9 ingests P8's C7 record, P7's analytics, P8's statements, FX and ledger costs, then
   measures), ``dispatch_reward`` (P9's outbox to P2) and ``learn`` (``POST /ar/update``,
   then the second ``POST /ar/cycles``) run at ``workflow.now()``.

**Negative paths (Gate R5) and where each is proven**

=============================================  ==========================================
Gate R5 negative path                          Proof
=============================================  ==========================================
A REJECTED candidate never reaches P6          P5's REJECTED is ``qa_rejected``, final:
                                               ``tests/unit/test_pipeline_http_qa.py::
                                               test_one_pass_carries_the_run_and_all_
                                               rejected_rejects_it``; P6 over HTTP refuses
                                               an uncleared recording:
                                               ``tests/integration/test_media_assemble_
                                               http.py::test_p6_refuses_a_recording_p5_
                                               did_not_clear``; P7/P8 re-verify clearance
                                               (R4): ``tests/unit/test_youtube_engine.py::
                                               test_a_track_p5_quarantined_never_reaches_
                                               youtube``, ``tests/unit/test_distribution_
                                               api.py::test_release_submission_refuses_an_
                                               uncleared_track``
A preflight failure never uploads              here: ``test_a_preflight_failure_never_
                                               uploads`` (the real upload step against
                                               api-youtube, inactive channel)
A duplicate webhook or reward is not           here: ``test_a_duplicate_reward_dispatch_
double-counted                                 is_not_counted_twice``; the outbox's
                                               duplicate delivery and lost-response paths:
                                               ``tests/integration/pipeline/test_gate_r3_
                                               outbox_http.py``
A missing seam field blocks                    ``tests/contract/test_c1_market_to_ar_
                                               consumer.py::test_p2_refuses_a_page_missing_
                                               a_fact``, ``test_c2_ar_to_experiments.py::
                                               test_consumer_refuses_a_payload_missing_any_
                                               field``, ``test_c3_experiments_to_music.py::
                                               test_p4_refuses_a_spec_missing_any_required_
                                               fact``, ``test_c4_music_to_qa.py::test_p5_
                                               refuses_an_event_missing_any_required_fact``
                                               (and C5-C8 alike in ``tests/contract``)
Budget exhaustion stops the run                ``tests/integration/pipeline/test_gate_r3_
                                               workflow.py::test_exhausted_budget_holds_
                                               the_run_and_never_calls_the_paid_activity``,
                                               ``test_r5_workflow_supply_and_budget.py::
                                               test_a_top_up_refused_by_budget_holds_and_
                                               reserves_each_write_once``
A hard stop is not overridable                 ``test_gate_r3_workflow.py::test_hard_stop_
                                               refuses_a_generic_approve_and_proceeds_only_
                                               on_external_evidence``; dashboard path:
                                               ``test_dashboard_live.py::test_dashboard_
                                               refuses_to_approve_a_hard_stop_and_signals_
                                               its_external_resolution``
A worker restart mid-run recovers              ``test_gate_r3_workflow.py::test_worker_
                                               killed_mid_activity_recovers_with_each_
                                               effect_exactly_once``
=============================================  ==========================================

The R3/R5 workflow tests use controlled mock steps; this module runs the real phases.

**Scope of the claim** (plan, R5): this proves the infrastructure and the posterior
update path work end to end on mock providers and the declared mock outcome model. It
does not prove the policy is economically optimal (the separate economics gate), and the
mock outcomes are a simulation, never evidence about a real audience.

Runtime: several minutes (P5 evaluates every candidate, P6 renders every playlist); see
``TIMEOUT_SECONDS``. Skips when the compose stack is unreachable; fails when the
time-skipping test server cannot start (there is no legitimate short horizon to fall
back to: the policy's horizon is 28 days).
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import sqlalchemy as sa
from plm_platform.config import SERVICE_TOKEN_HEADER, Settings
from plm_platform.db import database_url
from plm_platform.exceptions import ExceptionQueue
from plm_platform.ledger import BudgetLedger
from plm_platform.migration_safety import is_disposable_migration_target
from plm_platform.pipeline.activities import PlatformOperations, build_activities
from plm_platform.pipeline.client import pipeline_workflow_id
from plm_platform.pipeline.config import PublishConfig, load_pipeline_config
from plm_platform.pipeline.http import HttpPhaseSteps, PhaseClient, PhaseRequestRefused
from plm_platform.pipeline.states import PipelineState, Step
from plm_platform.pipeline.store import PipelineStore, StoredTransition
from plm_platform.pipeline.types import (
    PipelineInput,
    PipelineOptions,
    PipelineOutcome,
    PipelineStatus,
    StepRequest,
)
from plm_platform.pipeline.workflow import PipelineWorkflow
from plm_platform.service_urls import LOOPBACK_HOST, service_urls
from plm_platform.storage import build_client, ensure_bucket
from plm_platform.temporal import Phase
from plm_platform.testing.mock_outcomes import load_mock_outcome_model
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.storage import repositories as ar_repositories
from services.autonomous_ar.storage.models import (
    ar_allocation_policy,
    ar_arm_exposure,
    ar_arm_posterior,
    ar_content_arm_state,
    ar_experiment_result,
    ar_posterior_update_log,
    ar_reward_observation,
)
from services.market_intelligence.cli import run_daily, run_daily_step
from services.market_intelligence.storage.models import queries as market_queries
from tests.integration.pipeline.support import factory, settings
from tests.support.rights_detectors import register_controlled_references

__all__ = ["factory", "settings"]

pytestmark = [pytest.mark.integration, pytest.mark.full_chain, pytest.mark.timeout(5400)]

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAND = "neon_seoul"
"""P2's brand coordinate; the one brand P6's development registry declares."""
TASK_QUEUE = "r5-full-chain"
TIMEOUT_SECONDS = 3600
MIN_EFFECT_RATIO = Decimal("1.2")
CONFIG = load_pipeline_config(env={"PLM_PIPELINE_CONFIG_FILE": str(
    REPO_ROOT / "config" / "dev" / "pipeline.json")}, dotenv_path=REPO_ROOT / "none.env")
MODEL = load_mock_outcome_model(REPO_ROOT / "config" / "dev" / "mock_outcomes.json")
OPTIONS = PipelineOptions(step_start_to_close_seconds=3600.0, step_heartbeat_seconds=60.0,
                          step_maximum_attempts=6, platform_start_to_close_seconds=60.0,
                          platform_maximum_attempts=5)
TERMINAL = {"FAILED", "CANCELLED", "REJECTED", "HELD"}


# --- the stack ------------------------------------------------------------------------


def _urls() -> dict[Phase, str]:
    return service_urls(dotenv_path=REPO_ROOT / ".env", default_host=LOOPBACK_HOST)


def _r5_isolation_refusal(
    *, opted_in: bool, db: sa.engine.URL, endpoints: dict[Phase, str],
) -> str | None:
    """Pure pre-write safety gate, also testable without a running stack."""
    if not opted_in:
        return "r5_isolated_stack_opt_in_required"
    hosts = {"localhost", "127.0.0.1", "::1"}
    # The shared disposable-target rule (as `plm migrate` and raw Alembic), checked
    # on the configured URL before any connection exists.
    if db.get_backend_name() != "postgresql" or not is_disposable_migration_target(
        configured_database=db.database, connected_database=db.database,
        host=db.host, port=db.port,
    ):
        return "r5_isolated_postgres_required"
    if not endpoints or any(
        urlsplit(endpoint).scheme not in {"http", "https"}
        or urlsplit(endpoint).hostname not in hosts
        for endpoint in endpoints.values()
    ):
        return "r5_isolated_phase_endpoints_required"
    return None


@pytest.fixture(scope="module", autouse=True)
def _require_disposable_r5_stack(settings: Settings) -> None:
    """Fail before any destructive fixture can open the configured database."""
    refusal = _r5_isolation_refusal(
        opted_in=os.environ.get("PLM_R5_ISOLATED_STACK") == "1",
        db=database_url(settings), endpoints=_urls(),
    )
    if refusal == "r5_isolated_stack_opt_in_required":
        pytest.skip(
            "R5 mutates P1/P2/P7 and QA state; "
            "PLM_R5_ISOLATED_STACK=1 is required"
        )
    if refusal is not None:
        pytest.fail(f"R5 isolation guard: {refusal}")


def _token(settings: Settings) -> str:
    if settings.service_token is None:
        pytest.skip("PLM_SERVICE_TOKEN is not set")
    return settings.service_token.get_secret_value()


def _require_stack() -> None:
    for phase, url in _urls().items():
        try:
            ready = httpx.get(f"{url}/readyz", timeout=15.0)
        except httpx.HTTPError as exc:
            pytest.skip(f"{phase.name} API not reachable at {url}: {exc}; "
                        "run: docker compose up -d --wait")
        if ready.status_code != 200:
            pytest.skip(f"{phase.name} API not ready: {ready.status_code}")


class Api:
    """The phase APIs as an operator (and this test) call them: token, JSON, loopback."""

    def __init__(self, token: str) -> None:
        self._urls = _urls()
        self._headers = {SERVICE_TOKEN_HEADER: token}

    def call(self, method: str, phase: Phase, path: str, *, expect: tuple[int, ...] = (200,),
             **kwargs: Any) -> Any:
        answer = httpx.request(method, f"{self._urls[phase]}{path}", headers=self._headers,
                               timeout=120.0, **kwargs)
        assert answer.status_code in expect, (method, path, answer.status_code, answer.text)
        return answer.json()


def _steps(token: str, config: Any = CONFIG) -> HttpPhaseSteps:
    return HttpPhaseSteps({
        phase: PhaseClient(phase, httpx.Client(base_url=url, timeout=900.0), token=token)
        for phase, url in _urls().items()}, config=config)


# --- P2 learning state of the P1 arms (keyed by arm id alone) ---------------------------


def _reset_learning_state(engine: sa.Engine, arm_ids: list[str]) -> None:
    with engine.begin() as connection:
        for table in (ar_posterior_update_log, ar_reward_observation, ar_experiment_result,
                      ar_arm_exposure, ar_arm_posterior, ar_content_arm_state):
            connection.execute(table.delete().where(table.c.arm_id.in_(arm_ids)))


def _p1_arm_ids(api: Api) -> list[str]:
    page = api.call("GET", Phase.MARKET, "/market/v1/opportunities", params={"limit": 200})
    return [str(item["arm_id"]) for item in page["items"]]


# --- the run's facts --------------------------------------------------------------------


@dataclass
class Run:
    directive: dict[str, Any]
    workflow_id: str
    outcome: PipelineOutcome | None = None
    transitions: list[StoredTransition] = field(default_factory=list)

    @property
    def arm_id(self) -> str:
        return str(self.directive["arm_id"])

    def outputs(self, step: Step, to_state: PipelineState) -> dict[str, Any]:
        return next(t.outputs for t in self.transitions
                    if t.step == step.value and t.to_state is to_state
                    and t.error_code is None)


@dataclass
class Chain:
    t0: datetime
    first_cycle: dict[str, Any]
    first_decisions: list[dict[str, Any]]
    runs: list[Run]
    p1_rescored_at: datetime
    strong: str
    weak: str
    token: str
    engine: sa.Engine
    seconds: float


def _transitions(factory: sessionmaker[Session], workflow_id: str) -> list[StoredTransition]:
    with factory() as session:
        return PipelineStore().transitions(session, workflow_id)


async def _status(handle: WorkflowHandle[Any, Any]) -> PipelineStatus:
    result: PipelineStatus = await handle.query(PipelineWorkflow.status)
    return result


async def _drive(settings: Settings, factory: sessionmaker[Session], token: str,
                 api: Api) -> Chain:
    started = asyncio.get_running_loop().time()
    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # noqa: BLE001 - reported, never replaced by a fake horizon
        pytest.fail(f"Temporal's time-skipping test server could not start: {exc}")
    engine = factory.kw["bind"]
    try:
        t0 = await env.get_current_time()
        # 1. P1's daily run at T0 with the fixture-backed CI connector.
        with engine.begin() as connection:  # due again, as a fresh seed install leaves them
            connection.execute(sa.update(market_queries).values(next_run_at=None))
        daily = await asyncio.to_thread(run_daily, t0)
        assert daily["scores"]["scores"] >= 2, daily
        arms = _p1_arm_ids(api)
        assert len(arms) >= 2, arms

        # 2. P2's first allocation cycle, over HTTP (api-ar reads P1 over HTTP).
        learning = CONFIG.require_learning("r5-test")
        first = api.call("POST", Phase.AR, "/ar/cycles", json={
            "brand_id": BRAND, "slot_count": learning.slot_count, "budget": learning.budget,
            "as_of": t0.isoformat(), "policy_version": learning.policy_version,
            "activation_state": learning.activation_state,
            "random_seed": int(uuid.uuid4().int % 2**31)})
        decisions = api.call("GET", Phase.AR, f"/ar/cycles/{first['cycle_id']}/decisions")
        decision_ids = {d["decision_id"] for d in decisions}
        directives = [d for d in api.call("GET", Phase.AR, "/ar/directives",
                                          params={"brand_id": BRAND})
                      if d["decision_id"] in decision_ids]
        by_arm = {d["arm_id"]: d for d in directives}
        assert len(by_arm) >= 2, f"the first cycle allocated {sorted(by_arm)}; need two arms"
        effects = {arm: MODEL.effect(arm) for arm in by_arm}
        strong = max(effects, key=lambda a: effects[a])
        weak = min(effects, key=lambda a: effects[a])
        assert effects[strong] >= effects[weak] * MIN_EFFECT_RATIO, effects
        runs = [Run(directive=by_arm[a], workflow_id=pipeline_workflow_id(
            by_arm[a]["directive_id"])) for a in (strong, weak)]

        # 3. The pipeline, real HTTP activities, in the time-skipping server.
        platform = PlatformOperations(factory, queue=ExceptionQueue(),
                                      ledger=BudgetLedger(factory, currency="USD"))
        worker = Worker(env.client, task_queue=TASK_QUEUE, workflows=[PipelineWorkflow],
                        activities=build_activities(_steps(token), platform),
                        max_concurrent_activities=8)
        async with worker:
            handles = [await env.client.start_workflow(
                PipelineWorkflow.run,
                PipelineInput(directive_id=r.directive["directive_id"], brand_id=BRAND,
                              options=OPTIONS),
                id=r.workflow_id, task_queue=TASK_QUEUE) for r in runs]
            # No result is awaited yet, so time is not skipped: the runs work in real time
            # until each waits on its durable outcome timer.
            while True:
                states = [await _status(h) for h in handles]
                if any(s.state in TERMINAL for s in states):
                    raise AssertionError(
                        f"a run stopped before ANALYZING: {states}; transitions: "
                        f"{[_transitions(factory, r.workflow_id)[-1] for r in runs]}")
                if all(s.state == PipelineState.ANALYZING.value for s in states):
                    break
                await asyncio.sleep(5.0)
            # 4. P1's daily scoring on day 28 (the market moved on while the runs waited).
            matures = []
            for r in runs:
                analyzing = next(t for t in _transitions(factory, r.workflow_id)
                                 if t.to_state is PipelineState.ANALYZING)
                matures.append(datetime.fromisoformat(analyzing.outputs["matures_at"]))
            rescored_at = min(matures)
            await asyncio.to_thread(run_daily_step, "market.opportunity_scoring",
                                    payload={"now": rescored_at.isoformat()})
            # 5. Awaiting the results lets the test server skip the 28-day timers.
            # One at a time: each result() unlocks time skipping for its wait, and the
            # test server refuses a second concurrent unlock.
            outcomes = [await asyncio.wait_for(h.result(), TIMEOUT_SECONDS) for h in handles]
        for run, outcome in zip(runs, outcomes, strict=True):
            run.outcome = outcome
            run.transitions = _transitions(factory, run.workflow_id)
        return Chain(t0=t0, first_cycle=first, first_decisions=decisions, runs=runs,
                     p1_rescored_at=rescored_at, strong=strong, weak=weak, token=token,
                     engine=engine, seconds=asyncio.get_running_loop().time() - started)
    finally:
        await env.shutdown()


@pytest.fixture(scope="module")
def chain(settings: Settings, factory: sessionmaker[Session]) -> Iterator[Chain]:
    token = _token(settings)
    _require_stack()
    # A fresh stack has no bucket; on the dev stack an earlier storage test made it.
    ensure_bucket(settings=settings, client=build_client(settings))
    api = Api(token)
    engine: sa.Engine = factory.kw["bind"]
    now = datetime.now(UTC)
    with factory() as session:
        previous_policy = session.execute(sa.select(ar_allocation_policy.c.version).where(
            ar_allocation_policy.c.active.is_(True))).scalar_one_or_none()
        ar_repositories.insert_policy(session, DEFAULT_POLICY, brand_id=None, created_at=now)
        ar_repositories.activate_policy(session, DEFAULT_POLICY.version)
        session.commit()
        references = session.execute(sa.text(
            "SELECT count(*) FROM qa.reference_audio_fingerprints")).scalar_one()
    assert DEFAULT_POLICY.outcome_horizon.value == "28d"
    if not references:
        register_controlled_references(engine, now=now)
    publish = CONFIG.require_publish("r5-test")
    api.call("PUT", Phase.YOUTUBE, f"/youtube/channels/{publish.channel_id}",
             expect=(200, 201), json={
                 "channel_name": "PLM development mock channel", "brand_id": BRAND,
                 "oauth_credential_ref": "dev_mock_no_oauth",
                 "default_made_for_kids": False})
    mp = pytest.MonkeyPatch()
    mp.setenv("ENV", "ci")  # P1's fixture-backed connector, as in CI
    arm_ids = _p1_arm_ids(api)
    _reset_learning_state(engine, arm_ids)
    built: Chain | None = None
    try:
        built = asyncio.run(_drive(settings, factory, token, api))
        yield built
    finally:
        mp.undo()
        _reset_learning_state(engine, sorted(set(arm_ids) | set(_p1_arm_ids(api))))
        with engine.begin() as connection:  # the day-28 scores, dated in the future
            connection.execute(sa.text(
                "DELETE FROM market.market_opportunity_scores WHERE computed_at > :now"),
                {"now": datetime.now(UTC)})
        with factory() as session:
            if previous_policy is not None:
                ar_repositories.activate_policy(session, previous_policy)
            else:
                session.execute(sa.update(ar_allocation_policy).values(active=False))
            session.commit()


# --- Gate R5 claims -----------------------------------------------------------------------


@pytest.mark.timeout(TIMEOUT_SECONDS)
def test_every_run_completes_through_learning(chain: Chain) -> None:
    for run in chain.runs:
        assert run.outcome is not None and run.outcome.completed, run.outcome
        assert run.outcome.final_state == PipelineState.LEARNING.value
        states = [t.to_state.value for t in run.transitions]
        for state in ("IDEA", "RESEARCHED", "POLICY_CHECKED", "GENERATED", "PASSED",
                      "PACKAGED", "UPLOADING", "PROCESSING", "PUBLISHED", "ANALYZING",
                      "LEARNING"):
            assert state in states, (run.arm_id, state, states)
        assert not [t for t in run.transitions if t.to_state is PipelineState.HELD]
        last = run.transitions[-1]
        assert last.step == Step.LEARN.value and last.final
        # Every instant the workflow handed to a phase is workflow.now(): the outcome was
        # measured after the 28-day horizon, in skipped time, not in wall-clock time.
        published = datetime.fromisoformat(
            run.outputs(Step.AWAIT_PROCESSING, PipelineState.PUBLISHED)["published_at"])
        measured = datetime.fromisoformat(
            run.outputs(Step.EVALUATE_OUTCOME, PipelineState.LEARNING)["observed_at"])
        assert measured >= published + timedelta(days=28)
        learned = run.outputs(Step.LEARN, PipelineState.LEARNING)
        assert datetime.fromisoformat(learned["as_of"]) >= published + timedelta(days=28)
        rewards = run.outputs(Step.EVALUATE_OUTCOME, PipelineState.LEARNING)["rewards"]
        assert rewards and {r["status"] for r in rewards} == {"mature"}
        assert {r["arm_id"] for r in rewards} == {run.arm_id}
        assert {r["measurement_source"] for r in rewards} == {"synthetic"}
    assert chain.seconds < TIMEOUT_SECONDS
    print(f"chain: {chain.seconds:.0f} s wall; P1 rescored at {chain.p1_rescored_at}")


def _rewards(chain: Chain, arm: str) -> list[float]:
    run = next(r for r in chain.runs if r.arm_id == arm)
    return [float(r["normalized_reward"]) for r in
            run.outputs(Step.EVALUATE_OUTCOME, PipelineState.LEARNING)["rewards"]]


def _posterior(chain: Chain, arm: str) -> sa.RowMapping:
    with chain.engine.connect() as connection:
        return connection.execute(sa.select(ar_arm_posterior).where(
            ar_arm_posterior.c.arm_id == arm)).mappings().one()


def test_the_stronger_arm_earned_more_and_its_posterior_rose(chain: Chain) -> None:
    strong, weak = _rewards(chain, chain.strong), _rewards(chain, chain.weak)
    assert sum(strong) / len(strong) > sum(weak) / len(weak), (strong, weak)
    api = Api(chain.token)
    states = {arm: api.call("GET", Phase.AR, f"/ar/arms/{arm}/state")
              for arm in (chain.strong, chain.weak)}
    for arm, rewards in ((chain.strong, strong), (chain.weak, weak)):
        assert states[arm]["observation_count"] == len(rewards), states[arm]
        assert states[arm]["mature_result_count"] == len(rewards), states[arm]
    stored = _posterior(chain, chain.strong)
    print(f"rewards strong={chain.strong} {strong} weak={chain.weak} {weak}; posteriors "
          f"{ {a: (s['posterior_mean'], s['observation_count']) for a, s in states.items()} }; "
          f"strong prior {stored['prior_mean']}; weak prior "
          f"{_posterior(chain, chain.weak)['prior_mean']}")
    assert states[chain.strong]["posterior_mean"] > stored["prior_mean"], (states, stored)
    assert states[chain.strong]["posterior_mean"] > states[chain.weak]["posterior_mean"], \
        states


def _expected(decisions: list[dict[str, Any]], arm: str) -> float:
    return float(next(d for d in decisions if d["arm_id"] == arm)[
        "posterior_expected_reward"])


def _first_rank_probability(inputs: dict[str, dict[str, Any]], strong: str,
                            weak: str) -> float:
    """P(the strong arm's Thompson draw beats the weak arm's), from the stored draw inputs."""
    s, w = inputs[strong], inputs[weak]
    spread = math.sqrt(float(s["posterior_variance"]) + float(w["posterior_variance"]))
    z = (float(s["posterior_mean"]) - float(w["posterior_mean"])) / spread
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _draw_inputs(engine: sa.Engine, cycle_id: str) -> dict[str, dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(sa.text(
            "SELECT subject_ref, input_data_json FROM ops.decision_log "
            "WHERE decision_kind = 'allocation' "
            "AND CAST(input_data_json AS jsonb) ->> 'cycle_id' = :cycle"),
            {"cycle": cycle_id}).all()
    return {str(arm): dict(inputs) for arm, inputs in rows}


def test_the_second_cycle_shifts_allocation_toward_the_rewarded_arm(chain: Chain) -> None:
    """The learning step's cycle (the last one, which consumed every reward) against the
    first cycle: the posterior expected-reward gap moved toward the strong arm and the
    probability that Thompson sampling ranks it first rose."""
    api = Api(chain.token)
    last = max((r.outputs(Step.LEARN, PipelineState.LEARNING) for r in chain.runs),
               key=lambda o: o["as_of"])
    second_id = last["next_cycle"]["cycle_id"]
    second = api.call("GET", Phase.AR, f"/ar/cycles/{second_id}/decisions")
    assert last["update"]["applied"] >= 0
    first_gap = _expected(chain.first_decisions, chain.strong) - _expected(
        chain.first_decisions, chain.weak)
    second_gap = _expected(second, chain.strong) - _expected(second, chain.weak)
    print(f"expected-reward gap strong-weak: first cycle {first_gap:.4f}, second cycle "
          f"{second_gap:.4f}; second-cycle ranks "
          f"{ {d['arm_id']: (d['allocation_type'], d['rank_before_constraints'], d['rank_after_constraints']) for d in second} }")
    assert second_gap > 0 and second_gap > first_gap, (first_gap, second_gap)
    strong_row = next(d for d in second if d["arm_id"] == chain.strong)
    assert strong_row["allocation_type"] == "exploit", strong_row  # measured now
    assert strong_row["rank_after_constraints"] is not None, strong_row  # selected
    before = _draw_inputs(chain.engine, chain.first_cycle["cycle_id"])
    after = _draw_inputs(chain.engine, second_id)
    if {chain.strong, chain.weak} <= set(after):
        p_before = _first_rank_probability(before, chain.strong, chain.weak)
        p_after = _first_rank_probability(after, chain.strong, chain.weak)
        print(f"P(Thompson ranks the strong arm first): {p_before:.3f} -> {p_after:.3f}")
        assert p_after > p_before, (before, after)


def test_rewards_reached_p2_through_the_outbox(chain: Chain) -> None:
    with chain.engine.connect() as connection:
        for run in chain.runs:
            for reward in run.outputs(Step.EVALUATE_OUTCOME, PipelineState.LEARNING)[
                    "rewards"]:
                status = connection.execute(sa.text(
                    "SELECT status FROM analytics.outbox_events "
                    "WHERE CAST(payload_json AS jsonb) ->> 'experiment_id' = :e "
                    "AND CAST(payload_json AS jsonb) ->> 'content_id' = :c"),
                    {"e": reward["experiment_id"], "c": reward["content_id"]}).scalar_one()
                assert status == "delivered", reward
        assert {run.outputs(Step.DISPATCH_REWARD, PipelineState.LEARNING)[
            "delivery_status"] for run in chain.runs} == {"DELIVERED"}


# --- negative paths in this compose context ------------------------------------------------


def _learning_snapshot(engine: sa.Engine, arms: list[str]) -> dict[str, Any]:
    with engine.connect() as connection:
        posteriors = {row["arm_id"]: (row["posterior_mean"], row["posterior_variance"],
                                      row["observation_count"], row["update_version"])
                      for row in connection.execute(sa.select(ar_arm_posterior).where(
                          ar_arm_posterior.c.arm_id.in_(arms))).mappings()}
        observations = connection.execute(sa.select(sa.func.count()).select_from(
            ar_reward_observation).where(ar_reward_observation.c.arm_id.in_(arms))
        ).scalar_one()
        applied = connection.execute(sa.select(sa.func.count()).select_from(
            ar_posterior_update_log).where(ar_posterior_update_log.c.arm_id.in_(arms),
                                           ar_posterior_update_log.c.update_status
                                           == "applied")).scalar_one()
    return {"posteriors": posteriors, "observations": observations, "applied": applied}


def test_a_duplicate_reward_dispatch_is_not_counted_twice(chain: Chain) -> None:
    """Rerun the reward dispatch (the real step, a new idempotency key) and P2's update
    after the chain: P9 reports the facts delivered without a second delivery, P2 stores
    no second observation, the update applies nothing, and the posteriors are unchanged.
    A replay of the delivered payload straight to ``POST /ar/results`` is acknowledged
    as already stored."""
    arms = [chain.strong, chain.weak]
    before = _learning_snapshot(chain.engine, arms)
    steps = _steps(chain.token)
    for run in chain.runs:
        evaluated = run.outputs(Step.EVALUATE_OUTCOME, PipelineState.LEARNING)
        replay = asyncio.run(steps.dispatch_reward(StepRequest(
            workflow_id=run.workflow_id, directive_id=run.directive["directive_id"],
            brand_id=BRAND, step=Step.DISPATCH_REWARD.value,
            idempotency_key=f"{run.workflow_id}:dispatch_reward.duplicate-{uuid.uuid4().hex}",
            requested_at=datetime.fromisoformat(evaluated["observed_at"]),
            inputs={"evaluate_outcome": evaluated})))
        assert replay.outputs["delivery_status"] == "DELIVERED"
    api = Api(chain.token)
    experiments = [str(r.outputs(Step.RESEARCH, PipelineState.RESEARCHED)["experiment_id"])
                   for r in chain.runs]
    with chain.engine.connect() as connection:
        payloads = [row[0] for row in connection.execute(sa.text(
            "SELECT payload_json FROM analytics.outbox_events "
            "WHERE CAST(payload_json AS jsonb) ->> 'experiment_id' = ANY(:experiments)"),
            {"experiments": experiments})]
    assert len(payloads) == sum(
        len(r.outputs(Step.EVALUATE_OUTCOME, PipelineState.LEARNING)["rewards"])
        for r in chain.runs)
    for payload in payloads:
        stored = api.call("POST", Phase.AR, "/ar/results", json=payload)
        assert stored["stored"] is False, stored
    later = max(datetime.fromisoformat(r.outputs(Step.LEARN, PipelineState.LEARNING)["as_of"])
                for r in chain.runs) + timedelta(hours=1)
    update = api.call("POST", Phase.AR, "/ar/update", params={"as_of": later.isoformat()})
    assert update["applied"] == 0, update
    assert _learning_snapshot(chain.engine, arms) == before


def test_a_preflight_failure_never_uploads(chain: Chain) -> None:
    """The real upload step against api-youtube, for a package this chain assembled, on a
    channel an operator configured as inactive: P7's preflight refuses (422) and nothing
    reaches the publish path (no manifest, attempt or publication on that channel)."""
    channel = f"UC_r5_inactive_{uuid.uuid4().hex[:10]}"
    api = Api(chain.token)
    api.call("PUT", Phase.YOUTUBE, f"/youtube/channels/{channel}", expect=(201,), json={
        "channel_name": "R5 inactive channel", "brand_id": BRAND,
        "oauth_credential_ref": "dev_mock_no_oauth", "default_made_for_kids": False,
        "active": False})
    publish = CONFIG.require_publish("r5-test")
    steps = _steps(chain.token, dataclasses.replace(CONFIG, publish=PublishConfig(
        channel_id=channel, operating_mode=publish.operating_mode)))
    run = chain.runs[0]
    assembled = run.outputs(Step.ASSEMBLE, PipelineState.PACKAGED)
    with pytest.raises(PhaseRequestRefused) as refused:
        asyncio.run(steps.upload(StepRequest(
            workflow_id=run.workflow_id, directive_id=run.directive["directive_id"],
            brand_id=BRAND, step=Step.UPLOAD.value,
            idempotency_key=f"{run.workflow_id}:upload.preflight-{uuid.uuid4().hex}",
            requested_at=datetime.now(UTC), inputs={"assemble": assembled})))
    assert refused.value.phase is Phase.YOUTUBE and refused.value.status == 422
    assert refused.value.code == "PREFLIGHT_CHANNEL_MISSING".lower() or \
        "preflight" in refused.value.code, refused.value
    with chain.engine.connect() as connection:
        for table in ("publish_manifests", "publish_attempts", "publication_records"):
            count = connection.execute(sa.text(
                f"SELECT count(*) FROM youtube.{table} WHERE channel_id = :c"),
                {"c": channel}).scalar_one()
            assert count == 0, table
    with chain.engine.begin() as connection:
        connection.execute(sa.text(
            "DELETE FROM youtube.channel_profiles WHERE channel_id = :c"), {"c": channel})


# --- traceability: one lineage query over stored identifiers ------------------------------

LINEAGE_SQL = """
SELECT
  u.id                    AS p2_update,
  o.id                    AS p2_reward_observation,
  rp.id                   AS p9_reward,
  eo.id                   AS p9_outcome,
  ob.status               AS p9_outbox_status,
  pr.youtube_video_id     AS p7_video,
  mp.media_package_id     AS p6_package,
  pt.song_id              AS p6_track,
  rm.p5_manifest_id       AS p5_manifest,
  dr.release_id           AS p8_release,
  dt.isrc                 AS p8_isrc,
  rl.isrc                 AS p9_release_link,
  gc.id                   AS p4_candidate,
  ga.id                   AS p4_attempt,
  gp.id                   AS p4_plan,
  ts.track_spec_id        AS p3_track_spec,
  cp.package_id           AS p3_package,
  ex.experiment_id        AS p3_experiment,
  d.id                    AS p2_directive,
  ad.id                   AS p2_decision,
  dl.decision_key         AS p2_decision_log,
  fs.id                   AS p2_feature_snapshot,
  ms.id                   AS p1_score,
  ma.arm_id               AS p1_arm,
  mro.id                  AS p1_raw_observation,
  mo.id                   AS p1_observation
FROM ar.ar_posterior_update_log u
JOIN ar.ar_reward_observation o
  ON o.id = u.reward_observation_id AND o.arm_id = u.arm_id
LEFT JOIN analytics.rewards_posted rp
  ON rp.experiment_id = o.experiment_id AND rp.content_id = o.content_id
 AND rp.observation_window = o.observation_window AND rp.status = o.maturity
LEFT JOIN analytics.experiment_outcomes eo
  ON eo.experiment_id = o.experiment_id AND eo.unit_id = o.content_id
 AND eo.observation_window = o.observation_window AND eo.maturity = o.maturity
LEFT JOIN analytics.outbox_events ob
  ON CAST(ob.payload_json AS jsonb) ->> 'experiment_id' = o.experiment_id
 AND CAST(ob.payload_json AS jsonb) ->> 'content_id' = o.content_id
LEFT JOIN youtube.publication_records pr
  ON pr.youtube_video_id = o.content_id AND pr.experiment_id = o.experiment_id
 AND pr.content_arm_id = o.arm_id
LEFT JOIN media.media_packages mp
  ON mp.media_package_id = pr.media_package_id AND mp.experiment_id = o.experiment_id
LEFT JOIN media.playlist_tracks pt
  ON pt.playlist_session_id = mp.playlist_session_id
LEFT JOIN qa.release_manifests rm
  ON rm.candidate_id = pt.song_id AND rm.experiment_id = o.experiment_id
 AND rm.artifact_sha256 = pt.audio_checksum
LEFT JOIN distribution.releases dr
  ON dr.media_package_id = mp.media_package_id AND dr.experiment_id = o.experiment_id
 AND CAST(dr.source_manifest_ids AS jsonb) @> jsonb_build_array(rm.p5_manifest_id)
LEFT JOIN distribution.tracks dt
  ON dt.release_id = dr.release_id AND dt.audio_checksum = rm.artifact_sha256
LEFT JOIN analytics.release_links rl
  ON rl.release_id = dr.release_id AND rl.isrc = dt.isrc
 AND rl.media_package_id = mp.media_package_id
LEFT JOIN music.generated_candidates gc
  ON gc.id = rm.candidate_id AND gc.track_spec_id = rm.track_spec_id
LEFT JOIN music.generation_attempts ga
  ON ga.id = gc.source_attempt_id AND ga.generation_plan_id = gc.generation_plan_id
LEFT JOIN music.generation_plans gp
  ON gp.id = gc.generation_plan_id AND gp.track_spec_id = gc.track_spec_id
LEFT JOIN experiments.track_specs ts
  ON ts.track_spec_id = gc.track_spec_id AND ts.experiment_id = gc.experiment_id
LEFT JOIN experiments.content_packages cp
  ON cp.package_id = ts.package_id
LEFT JOIN experiments.experiments ex
  ON ex.experiment_id = cp.experiment_id AND ex.experiment_id = o.experiment_id
 AND ex.content_arm_id = o.arm_id
LEFT JOIN ar.ar_production_directive d
  ON CAST(d.id AS text) = ex.production_directive_id AND d.arm_id = o.arm_id
LEFT JOIN ar.ar_allocation_decision ad
  ON ad.id = d.decision_id AND ad.arm_id = d.arm_id
LEFT JOIN ops.decision_log dl
  ON dl.decision_key = 'allocation:' || CAST(d.id AS text) AND dl.subject_ref = d.arm_id
LEFT JOIN ar.ar_arm_feature_snapshot fs
  ON fs.id = ad.feature_snapshot_id AND fs.arm_id = d.arm_id
LEFT JOIN market.market_opportunity_scores ms
  ON ms.arm_id = fs.arm_id AND ms.as_of = fs.captured_at
LEFT JOIN market.market_arms ma
  ON ma.arm_id = ms.arm_id
LEFT JOIN market.market_raw_observations mro
  ON CAST(ms.evidence_json AS jsonb) -> 'observation_ids'
     @> jsonb_build_array(CAST(mro.id AS text))
LEFT JOIN market.market_observations mo
  ON mo.raw_observation_id = mro.id
WHERE u.update_status = 'applied' AND o.experiment_id = ANY(:experiments)
"""


def test_every_hop_traces_back_to_the_p1_observation_by_stored_ids(chain: Chain) -> None:
    """From each applied P2 posterior update back to P1, joined only on the identifiers
    each phase stored (no names, no fixtures): P2 update -> reward observation -> P9
    reward, outcome and outbox event -> P7 publication -> P6 package and playlist tracks
    -> P5 manifests -> P8 release and track ISRCs (and P9's release links) -> P4
    candidate, attempt, plan -> P3 track spec, package, experiment -> P2 directive,
    decision, decision log, feature snapshot -> P1 opportunity score, arm, the raw
    observations its evidence names and the normalized observations made from them.
    (P1's evidence ids are raw observation ids, not the ids its own
    ``/market/v1/observations/{id}`` route resolves; recorded in docs/STATUS.md.)
    A missing hop is a NULL column; every row must have every hop."""
    experiments = [str(r.outputs(Step.RESEARCH, PipelineState.RESEARCHED)["experiment_id"])
                   for r in chain.runs]
    with chain.engine.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            sa.text(LINEAGE_SQL), {"experiments": experiments}).mappings()]
    assert rows, "no applied posterior update for this run's experiments"
    for row in rows:
        missing = [hop for hop, value in row.items() if value is None]
        assert not missing, (missing, row)
        assert row["p9_outbox_status"] == "delivered"
        assert row["p8_isrc"] == row["p9_release_link"]
    # Every applied update, every published unit and every arm is covered.
    updates = {row["p2_update"] for row in rows}
    with chain.engine.connect() as connection:
        applied = connection.execute(sa.text(
            "SELECT count(*) FROM ar.ar_posterior_update_log u JOIN ar.ar_reward_observation o "
            "ON o.id = u.reward_observation_id WHERE u.update_status = 'applied' "
            "AND o.experiment_id = ANY(:experiments)"),
            {"experiments": experiments}).scalar_one()
    assert len(updates) == applied
    videos = {str(r["content_id"]) for run in chain.runs for r in
              run.outputs(Step.EVALUATE_OUTCOME, PipelineState.LEARNING)["rewards"]}
    assert {row["p7_video"] for row in rows} == videos
    assert {row["p1_arm"] for row in rows} == {chain.strong, chain.weak}
    assert {str(row["p2_directive"]) for row in rows} == {
        str(run.directive["directive_id"]) for run in chain.runs}
    # Each video's P8 ISRCs are exactly its playlist's tracks, one per track.
    by_video: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        by_video.setdefault(row["p7_video"], set()).add((row["p6_track"], row["p8_isrc"]))
    for pairs in by_video.values():
        assert len({t for t, _ in pairs}) == len({i for _, i in pairs}) == len(pairs)


def test_playlists_lie_in_their_declared_ranges_and_no_history_decided_qa(
        chain: Chain) -> None:
    """Each published package's length lies inside the duration range its P3 playlist
    spec declares (P6 never publishes short or long), and that range is the owner's
    45-75 min policy (P1's CI arms name no duration bucket, so P3 applies the policy;
    ADR-0036).

    The shared dev database keeps every earlier run's released recordings in P5's
    internal catalog. This run's mock audio is seeded from its own fresh track spec ids,
    so none of its candidates may match that history: a match would mean the result
    depended on what earlier runs left behind."""
    experiments = [str(r.outputs(Step.RESEARCH, PipelineState.RESEARCHED)["experiment_id"])
                   for r in chain.runs]
    with chain.engine.connect() as connection:
        rows = connection.execute(sa.text(
            "SELECT mp.media_package_id, mp.video_duration_seconds, ps.duration_min_seconds, "
            "ps.duration_max_seconds FROM media.media_packages mp "
            "JOIN youtube.publication_records pr ON pr.media_package_id = mp.media_package_id "
            "JOIN media.playlist_tracks pt ON pt.playlist_session_id = mp.playlist_session_id "
            "JOIN experiments.track_specs ts ON ts.track_spec_id = ("
            "  SELECT rm.track_spec_id FROM qa.release_manifests rm "
            "  WHERE rm.candidate_id = pt.song_id) "
            "JOIN experiments.playlist_specs ps ON ps.package_id = ts.package_id "
            "WHERE mp.experiment_id = ANY(:experiments) "
            "GROUP BY 1, 2, 3, 4"), {"experiments": experiments}).all()
        catalog_hits = connection.execute(sa.text(
            "SELECT count(*) FROM qa.quarantine_cases q "
            "JOIN music.generated_candidates gc ON gc.id = q.candidate_id "
            "WHERE gc.experiment_id = ANY(:experiments) "
            "AND q.reason_codes_json LIKE '%INTERNAL_CATALOG_AUDIO_MATCH%'"),
            {"experiments": experiments}).scalar_one()
    published = {row[0] for row in rows}
    assert len(published) == sum(
        len(r.outputs(Step.ASSEMBLE, PipelineState.PACKAGED)["packages"]) for r in chain.runs)
    for package, seconds, low, high in rows:
        assert (low, high) == (2700, 4500), (package, low, high)
        assert low <= seconds <= high, (package, seconds, low, high)
    assert catalog_hits == 0

"""P4 paid generation goes through the write guard, and P4 mutations replay by key (R5).

In-process: the engine and the FastAPI routes over SQLite, with counting provider fakes.
Proves that a real (non-mock) provider is never called unless the guard answers
``EXECUTE`` (live policy, an authorization and a budget reservation), that a funded call
is settled against the reservation, that the mock provider simulates without budget, that
re-executing a plan never pays twice, and that ``Idempotency-Key`` replays plan / execute
/ export. It does not prove a real provider's behaviour (R6) or the persisted ledger
(``tests/integration/test_ledger_postgres.py`` covers that).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plm_contracts import ExecutionPolicy, ProviderMode
from plm_contracts.money import Money
from plm_contracts.seams.c3 import TrackSpecPayload
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.execution import (
    BudgetVerdict,
    CostEstimate,
    ExecutionSettings,
    ExternalWriteGuard,
    WriteAuthorization,
)

from services.music_factory.api.app import create_app
from services.music_factory.config import load_music_factory_settings
from services.music_factory.domain.enums import AttemptStatus, PlanStatus
from services.music_factory.domain.models import GenerationRequest, GenerationResponse
from services.music_factory.generation.engine import GENERATE_CAPABILITY, GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.providers.adapters import SunoAdapter
from services.music_factory.providers.mock_provider import MockMusicProvider
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.repositories import (
    get_generation_plan,
    list_generation_attempts,
)
from tests.support.music_db import create_music_engine, music_db_session, music_session
from tests.support.seams import lineage_fields, track_spec_fields

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
TOKEN = "music-seam-token"
HEADERS = {SERVICE_TOKEN_HEADER: TOKEN}
ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": TOKEN,
}


def _spec(track_spec_id: str = "trk_guard_01", **overrides: Any) -> dict[str, Any]:
    return TrackSpecPayload.build(**track_spec_fields(
        track_spec_id=track_spec_id, requested_duration=20,
        duration_range={"min_seconds": 10, "max_seconds": 30}, candidate_target=2,
        lineage=lineage_fields(experiment_id="exp_guard_01"), **overrides,
    )).to_wire()


class CountingRealProvider(MockMusicProvider):
    """A provider that declares itself REAL (a paid external call) and counts calls."""

    provider_mode = ProviderMode.REAL

    def __init__(self, *, fail_before_job: bool = False) -> None:
        super().__init__(provider_id="real_fake", model_id="real-fake-1")
        self.calls = 0
        self.fail_before_job = fail_before_job

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls += 1
        if self.fail_before_job:
            return GenerationResponse(provider_job_id="", status=AttemptStatus.FAILED,
                                      error_message="quota_exceeded")
        return super().generate(request)


class CountingMockProvider(MockMusicProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls += 1
        return super().generate(request)


@dataclass
class RecordingBudget:
    """A budget gate that funds every write and records what was settled."""

    allow: bool = True
    reserved: list[str] = field(default_factory=list)
    committed: list[tuple[str, Money | None]] = field(default_factory=list)
    released: list[tuple[str, str]] = field(default_factory=list)

    def reserve(self, capability: str, idempotency_key: str,
                cost_estimate: CostEstimate) -> BudgetVerdict:
        assert capability == GENERATE_CAPABILITY
        assert cost_estimate is None  # P4 knows credits, not money: the rate card prices it
        if not self.allow:
            return BudgetVerdict(allowed=False, reason="exhausted")
        self.reserved.append(idempotency_key)
        return BudgetVerdict(allowed=True, reason="reserved")

    def commit(self, capability: str, idempotency_key: str, *, actual: Money | None) -> None:
        self.committed.append((idempotency_key, actual))

    def release(self, capability: str, idempotency_key: str, *, reason: str) -> None:
        self.released.append((idempotency_key, reason))


def _live_guard(budget: Any = None) -> ExternalWriteGuard:
    return ExternalWriteGuard(
        ExecutionSettings(policies={GENERATE_CAPABILITY: ExecutionPolicy.LIVE}),
        authorizations=[WriteAuthorization(GENERATE_CAPABILITY, granted_by="op", granted_at=NOW)],
        budget=budget,
    )


def _run(guard: ExternalWriteGuard, provider: Any) -> tuple[Any, list[Any], Any]:
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                              write_guard=guard)
    with music_db_session() as session:
        result = engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                                     provider_adapter=provider)
        attempts = list_generation_attempts(session, plan.plan_id)
        stored = get_generation_plan(session, plan.plan_id)
    return result, attempts, stored


# -- the guard ---------------------------------------------------------------------------


def test_live_without_budget_never_calls_the_provider() -> None:
    """Proves LIVE policy plus an authorization is not enough: with no budget ledger the
    guard denies (``budget_ledger_unavailable``), the provider is never called, nothing is
    recorded as attempted, and the plan stops FAILED with the reason code."""
    provider = CountingRealProvider()
    result, attempts, stored = _run(_live_guard(), provider)
    assert provider.calls == 0
    assert result.stopped_reason == "budget_ledger_unavailable"
    assert result.status is PlanStatus.FAILED and stored.status is PlanStatus.FAILED
    assert attempts == [] and result.candidates == []


@pytest.mark.parametrize(("guard", "reason"), [
    (ExternalWriteGuard(), "policy_dry_run"),
    (ExternalWriteGuard(ExecutionSettings(policies={GENERATE_CAPABILITY: ExecutionPolicy.LIVE})),
     "no_authorization"),
    (_live_guard(RecordingBudget(allow=False)), "exhausted"),
])
def test_every_other_refusal_also_stops_before_the_call(guard: ExternalWriteGuard,
                                                        reason: str) -> None:
    """Proves dry-run, a missing authorization and an exhausted budget each stop the plan
    before a real provider is called, with the guard's reason code."""
    provider = CountingRealProvider()
    result, attempts, _ = _run(guard, provider)
    assert provider.calls == 0
    assert result.stopped_reason == reason
    assert attempts == []


def test_a_funded_call_is_reserved_under_the_attempt_id_and_committed() -> None:
    """Proves an EXECUTE decision reserves per attempt id and commits after the call (at the
    reserved estimate: the provider reports credits, not money)."""
    budget = RecordingBudget()
    provider = CountingRealProvider()
    result, attempts, _ = _run(_live_guard(budget), provider)
    assert result.stopped_reason is None and len(result.candidates) == 2
    assert provider.calls == 2
    assert budget.reserved == [a.attempt_id for a in attempts]
    assert budget.committed == [(a.attempt_id, None) for a in attempts]
    assert budget.released == []


def test_a_provider_refusal_before_a_job_releases_the_reservation() -> None:
    """Proves a call the provider refused without creating a job is released, not spent."""
    budget = RecordingBudget()
    provider = CountingRealProvider(fail_before_job=True)
    _run(_live_guard(budget), provider)
    assert provider.calls >= 1
    assert budget.committed == []
    assert [key for key, _ in budget.released] == budget.reserved
    assert {reason for _, reason in budget.released} == {"provider_refused_before_job"}


def test_the_mock_provider_simulates_without_budget_even_under_live_policy() -> None:
    """Proves a ``ProviderMode.MOCK`` provider needs no budget: the guard answers SIMULATE."""
    guard = _live_guard()  # LIVE, authorized, but NoBudgetLedger
    provider = CountingMockProvider()
    result, _, _ = _run(guard, provider)
    assert provider.calls == 2 and len(result.candidates) == 2
    assert {d.decision.value for d in guard.decisions} == {"simulate"}


def test_re_executing_a_plan_never_pays_twice() -> None:
    """Proves a second run of a completed plan reuses its candidates and calls nothing."""
    budget = RecordingBudget()
    provider = CountingRealProvider()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                              write_guard=_live_guard(budget))
    with music_db_session() as session:
        first = engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                                    provider_adapter=provider)
        second = engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                                     provider_adapter=provider)
    assert provider.calls == 2
    assert [c.candidate_id for c in second.candidates] == \
        [c.candidate_id for c in first.candidates]
    assert len(budget.reserved) == 2


# -- the HTTP surface --------------------------------------------------------------------


def _client(*, default_provider: str = "mock",
            guard: ExternalWriteGuard | None = None) -> TestClient:
    from services.music_factory.storage.artifacts import MinioArtifactStorage
    from tests.support.fake_minio import FakeMinio

    platform = load_settings(env=dict(ENV))
    settings = load_music_factory_settings(platform=platform, default_provider=default_provider)
    engine = create_music_engine(static_pool=True)
    storage = MinioArtifactStorage(platform, client=FakeMinio())  # type: ignore[arg-type]
    app = create_app(session_factory=lambda: music_session(engine), settings=settings,
                     storage=storage, write_guard=guard or ExternalWriteGuard(),
                     clock=FixedClock(NOW))
    return TestClient(app)


def test_api_refuses_an_unfunded_real_call_with_409_and_never_calls(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves the API maps a guard refusal to 409 in the platform convention shared with
    P7/P8 (``external_write_<decision>``, "music.generate not performed: <reason>"), and
    that the real adapter's ``generate`` is never reached."""
    def unreachable(self: SunoAdapter, request: GenerationRequest) -> GenerationResponse:
        raise AssertionError("the real provider must not be called")

    monkeypatch.setattr(SunoAdapter, "generate", unreachable)
    client = _client(default_provider="suno", guard=_live_guard())
    plan = client.post("/music/plans", json=_spec(), headers=HEADERS).json()
    resp = client.post(f"/music/plans/{plan['plan_id']}/execute", json=_spec(), headers=HEADERS)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "external_write_deny"
    assert detail["message"] == "music.generate not performed: budget_ledger_unavailable"
    assert detail["reason"] == "budget_ledger_unavailable"
    assert detail["decision"] == "deny"
    assert [w["reason"] for w in detail["external_writes"]] == ["budget_ledger_unavailable"]
    stored =client.get(f"/music/plans/{plan['plan_id']}", headers=HEADERS).json()
    assert stored["plan"]["status"] == "failed"

    dry = _client(default_provider="suno")  # no configured policy: dry-run
    plan = dry.post("/music/plans", json=_spec(), headers=HEADERS).json()
    resp = dry.post(f"/music/plans/{plan['plan_id']}/execute", json=_spec(), headers=HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "policy_dry_run"


def test_api_replays_plan_execute_and_export_by_idempotency_key() -> None:
    """Proves a retried plan / execute / export with the same key returns the first answer
    (no duplicate insert, no second generation), and the same key with another body is
    409 ``idempotency_key_reused``."""
    client = _client()
    spec = _spec()

    def keyed(key: str) -> dict[str, str]:
        return {**HEADERS, "Idempotency-Key": key}

    plan = client.post("/music/plans", json=spec, headers=keyed("plan-1"))
    plan_again = client.post("/music/plans", json=spec, headers=keyed("plan-1"))
    assert plan.status_code == plan_again.status_code == 200
    assert plan.json() == plan_again.json()

    reused = client.post("/music/plans", json=_spec("trk_guard_02"), headers=keyed("plan-1"))
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "idempotency_key_reused"

    url = f"/music/plans/{plan.json()['plan_id']}/execute"
    run = client.post(url, json=spec, headers=keyed("exec-1"))
    run_again = client.post(url, json=spec, headers=keyed("exec-1"))
    assert run.status_code == run_again.status_code == 200
    assert run.json() == run_again.json()
    receipts = run.json()["external_writes"]
    assert [(w["capability"], w["decision"], w["reason"]) for w in receipts] == \
        [("music.generate", "simulate", "mock_provider")] * 2
    attempts = client.get(
        f"/music/candidates/{run.json()['candidates'][0]['candidate_id']}/attempts",
        headers=HEADERS).json()
    assert len(attempts) == run.json()["attempts_count"] == 2

    candidate_id = run.json()["candidates"][0]["candidate_id"]
    export_url = f"/music/candidates/{candidate_id}/export-to-p5"
    exported = client.post(export_url, headers=keyed("export-1"))
    exported_again = client.post(export_url, headers=keyed("export-1"))
    assert exported.status_code == exported_again.status_code == 200
    assert exported.json() == exported_again.json()

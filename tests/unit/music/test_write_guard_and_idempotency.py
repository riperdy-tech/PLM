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

import json
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
from plm_platform.ledger import BudgetLedger, PersistentBudgetGate

from services.music_factory.api.app import create_app
from services.music_factory.config import default_provider_for, load_music_factory_settings
from services.music_factory.domain.enums import AttemptStatus, CandidateStatus, PlanStatus
from services.music_factory.domain.models import (
    GeneratedCandidate,
    GenerationAttempt,
    GenerationRequest,
    GenerationResponse,
)
from services.music_factory.generation.engine import (
    GENERATE_CAPABILITY,
    GenerationEngine,
    GenerationPlanSpecMismatch,
)
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.providers.adapters import SunoAdapter
from services.music_factory.providers.mock_provider import MockMusicProvider
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.repositories import (
    get_generation_plan,
    insert_candidate,
    insert_creative_variant,
    insert_generation_attempt,
    insert_generation_plan,
    list_generation_attempts,
)
from tests.support.music_db import create_music_engine, music_db_session, music_session
from tests.support.ops_db import create_ops_engine, ops_session_factory
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
    fields = track_spec_fields(
        track_spec_id=track_spec_id, requested_duration=20,
        duration_range={"min_seconds": 10, "max_seconds": 30}, candidate_target=2,
        lineage=lineage_fields(experiment_id="exp_guard_01"), **overrides,
    )
    if "provider_policy" not in overrides:
        # CountingRealProvider and SunoAdapter are explicitly authorized
        # by this fixture so the tests can isolate budget/guard refusals.
        fields["provider_policy"] = {
            "allowed_providers": ["mock", "real_fake", "suno"],
        }
    return TrackSpecPayload.build(**fields).to_wire()


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
            return GenerationResponse(
                provider_job_id="", status=AttemptStatus.FAILED,
                error_message="quota_exceeded",
                confirmed_unsubmitted=True,  # Fixture explicitly rejected before a job.
            )
        result = super().generate(request)
        if result.status is AttemptStatus.SUCCEEDED:
            # Test-only fake receipt: real adapters must return their own
            # observed billing reference; the engine never invents one.
            return result.model_copy(update={
                "credit_receipt_ref": f"fixture-credit-receipt-{self.calls}",
            })
        return result


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


@pytest.mark.parametrize("provider_kind", ["real", "mock"])
def test_real_provider_allowlist_and_mock_simulation_are_separate(
    provider_kind: str,
) -> None:
    """P3 may restrict providers independently of global execution permission."""
    provider = CountingRealProvider() if provider_kind == "real" else CountingMockProvider()
    budget = RecordingBudget()
    guard = _live_guard(budget)
    spec = adapt_track_spec(_spec(provider_policy={"allowed_providers": ["other_provider"]}))
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(
        clock=FixedClock(NOW), storage=InMemoryArtifactStorage(), write_guard=guard,
    )
    with music_db_session() as session:
        result = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec, provider_adapter=provider,
        )
        attempts = list_generation_attempts(session, plan.plan_id)
        persisted = get_generation_plan(session, plan.plan_id)
        assert persisted is not None
    if provider_kind == "real":
        assert attempts == [] and persisted.status is PlanStatus.FAILED
        assert result.status is PlanStatus.FAILED
        assert result.stopped_reason == "plan_provider_not_allowed"
        assert [d.reason for d in guard.decisions] == ["plan_provider_not_allowed"]
        assert provider.calls == 0
    else:
        # A mock is an explicit SIMULATE decision, not a paid write under
        # a real provider's P3 entitlement, and retains mock provenance.
        assert result.status is PlanStatus.COMPLETED
        assert len(attempts) == len(result.candidates) == provider.calls == 2
        assert {attempt.provider_id for attempt in attempts} == {"mock"}
        assert {d.reason for d in guard.decisions} == {"mock_provider"}
    assert budget.reserved == []


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


@pytest.mark.parametrize(("field", "reason"), [
    ("budget_limit", "plan_money_budget_exhausted"),
    ("credit_limit", "plan_credit_budget_exhausted"),
])
def test_zero_plan_limits_restrict_real_provider(field: str, reason: str) -> None:
    budget = RecordingBudget()
    guard = _live_guard(budget)
    provider = CountingRealProvider()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    plan = plan.model_copy(update={field: 0.0})
    engine = GenerationEngine(
        clock=FixedClock(NOW), storage=InMemoryArtifactStorage(), write_guard=guard,
    )
    with music_db_session() as session:
        result = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec, provider_adapter=provider,
        )
        assert list_generation_attempts(session, plan.plan_id) == []
    assert provider.calls == 0 and budget.reserved == []
    assert result.stopped_reason == reason and result.status is PlanStatus.FAILED
    assert [decision.reason for decision in guard.decisions] == [reason]


@pytest.mark.parametrize(
    ("receipt_present", "credit_limit", "reason"),
    [
        (True, 1.0, "plan_credit_budget_exhausted"),
        (False, 100.0, "plan_credit_usage_reconciliation_required"),
    ],
)
def test_prior_real_credit_exhaustion_or_unknown_usage_blocks_next_paid_call(
    receipt_present: bool, credit_limit: float, reason: str,
) -> None:
    """Previously accepted work keeps its plan's credit headroom unavailable.

    A resumed plan must apply the same rule without paying again. This is
    an in-process safety brake, not concurrent/estimated credit reservation.
    """
    class OptionalReceiptProvider(CountingRealProvider):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            result = super().generate(request)
            if not receipt_present and result.status is AttemptStatus.SUCCEEDED:
                return result.model_copy(update={"credit_receipt_ref": None})
            return result

    provider = OptionalReceiptProvider()
    budget = RecordingBudget()
    guard = _live_guard(budget)
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    plan = plan.model_copy(update={"credit_limit": credit_limit})
    engine = GenerationEngine(
        clock=FixedClock(NOW), storage=InMemoryArtifactStorage(), write_guard=guard,
    )
    with music_db_session() as session:
        first = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec, provider_adapter=provider,
        )
        attempts = list_generation_attempts(session, plan.plan_id)
        assert len(attempts) == 1
        assert attempts[0].credit_basis == (
            "provider_reported" if receipt_present else "unknown"
        )
        assert attempts[0].actual_credits == (1.0 if receipt_present else None)
        assert first.status is PlanStatus.FAILED
        assert first.stopped_reason == reason
        assert len(first.candidates) == 1

        replay = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec, provider_adapter=provider,
        )
        assert replay.status is PlanStatus.FAILED
        assert replay.stopped_reason == reason
        assert len(list_generation_attempts(session, plan.plan_id)) == 1
    assert provider.calls == 1
    assert len(budget.reserved) == len(budget.committed) == 1
    assert budget.released == []
    assert [d.reason for d in guard.decisions] == ["authorized", reason, reason]


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
    assert {reason for _, reason in budget.released} == {"provider_confirmed_unsubmitted"}


def test_live_failure_without_job_id_or_no_charge_proof_holds_reservation() -> None:
    """A missing job ID after submission is not a confirmed free refusal."""
    class AmbiguousNoJob(CountingRealProvider):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.calls += 1
            return GenerationResponse(
                provider_job_id="", status=AttemptStatus.FAILED,
                error_message="response timed out after submission",
            )

    provider = AmbiguousNoJob()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = create_music_engine(static_pool=True)
    try:
        budget = RecordingBudget()
        guard = _live_guard(budget)
        with music_session(engine) as session:
            first = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=guard,
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            attempts = list_generation_attempts(session, plan.plan_id)
            assert len(attempts) == 1
            assert attempts[0].failure_code == "PROVIDER_OUTCOME_UNKNOWN"
            session.commit()
        assert first.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in first.errors
        assert provider.calls == 1
        assert len(budget.reserved) == 1
        assert budget.committed == [] and budget.released == []

        fresh_budget = RecordingBudget()
        fresh_guard = _live_guard(fresh_budget)
        with music_session(engine) as session:
            replay = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=fresh_guard,
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            assert len(list_generation_attempts(session, plan.plan_id)) == 1
            session.rollback()
        assert "provider_job_reconciliation_required" in replay.errors
        assert provider.calls == 1
        assert fresh_budget.reserved == [] and fresh_guard.decisions == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("change", [
    {"status": AttemptStatus.SUCCEEDED},
    {"provider_job_id": "some-job"},
    {"audio_data": b"RIFF"},
    {"cost_credits": 1.0},
    {"credit_receipt_ref": "receipt"},
])
def test_no_charge_confirmation_rejects_contradictory_provider_evidence(
    change: dict[str, object],
) -> None:
    """A confirmed pre-submission refusal cannot have a job or charge."""
    with pytest.raises(ValueError, match="confirmed_unsubmitted"):
        GenerationResponse.model_validate({
            "provider_job_id": "", "status": AttemptStatus.FAILED,
            "confirmed_unsubmitted": True, **change,
        })


def test_ambiguous_no_job_response_keeps_persisted_ops_reservation() -> None:
    """The durable ledger must not refund a failed response lacking a receipt."""
    class UnknownReceiptProvider(CountingRealProvider):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.calls += 1
            return GenerationResponse(
                provider_job_id="", status=AttemptStatus.FAILED,
                error_message="no provider receipt after submission",
            )

    ops_engine = create_ops_engine()
    music_engine = create_music_engine(static_pool=True)
    try:
        ledger = BudgetLedger(ops_session_factory(ops_engine), currency="USD")
        month_start = datetime(2026, 10, 1, tzinfo=UTC)
        ledger.set_ceiling(
            limit=Money.of("3", "USD"), period_start=month_start,
            period_end=datetime(2026, 11, 1, tzinfo=UTC),
            set_by="unit-test", now=NOW,
        )
        ledger.set_rate(
            capability=GENERATE_CAPABILITY, cost=Money.of("1", "USD"),
            effective_from=month_start, set_by="unit-test", now=NOW,
        )
        provider = UnknownReceiptProvider()
        spec = adapt_track_spec(_spec())
        plan, variants = plan_generation(spec, created_at=NOW)
        with music_session(music_engine) as session:
            first = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=_live_guard(
                    PersistentBudgetGate(ledger, clock=FixedClock(NOW))
                ),
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            session.commit()
        assert first.status is PlanStatus.FAILED
        assert len(ledger.entries()) == 1
        assert ledger.entries()[0].status.value == "reserved"

        with music_session(music_engine) as session:
            replay = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=_live_guard(
                    PersistentBudgetGate(ledger, clock=FixedClock(NOW))
                ),
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            session.rollback()
        assert replay.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in replay.errors
        assert provider.calls == 1
        assert len(ledger.entries()) == 1
        assert ledger.entries()[0].status.value == "reserved"
    finally:
        music_engine.dispose()
        ops_engine.dispose()


def test_suno_submit_error_keeps_real_guard_budget_and_blocks_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual REAL adapter must not invent a job or enable a paid retry."""
    from services.music_factory.providers.suno_client import SunoClientError

    provider = SunoAdapter(api_key="dummy-test-key")
    calls = 0

    def submit_failure(**kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise SunoClientError("submission timeout after request was sent")

    monkeypatch.setattr(provider._client, "submit_generation", submit_failure)
    budget = RecordingBudget()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = create_music_engine(static_pool=True)
    try:
        with music_session(engine) as session:
            first = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=_live_guard(budget),
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            attempts = list_generation_attempts(session, plan.plan_id)
            assert len(attempts) == 1
            assert attempts[0].provider_job_id == ""
            assert attempts[0].failure_code == "PROVIDER_OUTCOME_UNKNOWN"
            session.commit()
        assert first.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in first.errors
        assert calls == 1 and len(budget.reserved) == 1
        assert budget.committed == [] and budget.released == []

        new_budget = RecordingBudget()
        with music_session(engine) as session:
            replay = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=_live_guard(new_budget),
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            session.rollback()
        assert "provider_job_reconciliation_required" in replay.errors
        assert calls == 1 and new_budget.reserved == []
    finally:
        engine.dispose()


def test_failed_live_job_refuses_automatic_retry() -> None:
    """A provider job ID on a failed call requires reconciliation."""
    class FailedWithJob(CountingRealProvider):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.calls += 1
            return GenerationResponse(
                provider_job_id="job-accepted",
                status=AttemptStatus.FAILED,
                error_message="outcome uncertain",
            )

    budget = RecordingBudget()
    provider = FailedWithJob()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(
        clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
        write_guard=_live_guard(budget),
    )
    with music_db_session() as session:
        first = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec,
            provider_adapter=provider,
        )
        replay = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec,
            provider_adapter=provider,
        )
        assert len(list_generation_attempts(session, plan.plan_id)) == 1
    assert first.status is PlanStatus.FAILED
    assert replay.status is PlanStatus.FAILED
    assert "provider_job_reconciliation_required" in first.errors
    assert "provider_job_reconciliation_required" in replay.errors
    assert first.stopped_reason is None
    assert provider.calls == 1
    assert len(budget.reserved) == len(budget.committed) == 1
    assert budget.released == []


@pytest.mark.parametrize("missing_job_id", ["", "   "])
def test_paid_success_with_audio_but_no_job_receipt_cannot_export_or_rebill(
    missing_job_id: str,
) -> None:
    """Usable audio is not a verified paid-provider origin without its job ID."""
    class AcceptedWithoutReceipt(CountingRealProvider):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            result = super().generate(request)
            return result.model_copy(update={"provider_job_id": missing_job_id})

    provider = AcceptedWithoutReceipt()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    db = create_music_engine(static_pool=True)
    try:
        initial_budget = RecordingBudget()
        with music_session(db) as session:
            first = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=_live_guard(initial_budget),
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            attempts = list_generation_attempts(session, plan.plan_id)
            assert len(attempts) == 1
            assert attempts[0].status is AttemptStatus.FAILED
            assert attempts[0].provider_job_id == ""
            assert attempts[0].failure_code == "PROVIDER_ACCEPTED_JOB_ID_MISSING"
            session.commit()
        assert first.status is PlanStatus.FAILED
        assert first.candidates == [] and first.artifacts == []
        assert first.rights_snapshots == []
        assert "provider_job_reconciliation_required" in first.errors
        assert provider.calls == 1
        assert len(initial_budget.reserved) == len(initial_budget.committed) == 1
        assert initial_budget.released == []

        later_budget = RecordingBudget()
        with music_session(db) as session:
            later_guard = _live_guard(later_budget)
            replay = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=later_guard,
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            assert len(list_generation_attempts(session, plan.plan_id)) == 1
            session.rollback()
        assert replay.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in replay.errors
        assert provider.calls == 1
        assert later_budget.reserved == [] and later_guard.decisions == []
    finally:
        db.dispose()


@pytest.mark.parametrize("provider_job_id", ["", "accepted-but-output-missing"])
def test_paid_success_without_audio_cannot_be_rebilled_after_restart(
    provider_job_id: str,
) -> None:
    """Accepted paid output without audio stays unresolved even with no provider job ID."""
    class AcceptedWithoutAudio(CountingRealProvider):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.calls += 1
            return GenerationResponse(
                provider_job_id=provider_job_id,
                status=AttemptStatus.SUCCEEDED,
                audio_data=None,
            )

    provider = AcceptedWithoutAudio()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = create_music_engine(static_pool=True)
    try:
        first_budget = RecordingBudget()
        with music_session(engine) as session:
            first = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=_live_guard(first_budget),
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            previous = list_generation_attempts(session, plan.plan_id)
            assert len(previous) == 1
            assert previous[0].status is AttemptStatus.FAILED
            assert previous[0].failure_code == "PROVIDER_ACCEPTED_OUTPUT_MISSING"
            assert previous[0].provider_job_id == provider_job_id
            session.commit()
        assert first.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in first.errors
        assert provider.calls == 1
        assert len(first_budget.reserved) == len(first_budget.committed) == 1
        assert first_budget.released == []

        # Fresh guard, fresh session, and a globally permissive new budget:
        # persisted attempt evidence, not in-memory ledger state, stops replay.
        replay_budget = RecordingBudget()
        with music_session(engine) as session:
            replay_guard = _live_guard(replay_budget)
            replay = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=replay_guard,
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            assert len(list_generation_attempts(session, plan.plan_id)) == 1
            session.rollback()
        assert replay.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in replay.errors
        assert provider.calls == 1
        assert replay_budget.reserved == []
        assert replay_guard.decisions == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("charge_evidence", [
    {
        "credit_basis": "provider_reported", "actual_credits": 1.0,
        "credit_receipt_ref": "bill-credit-1",
    },
    {
        "money_basis": "provider_reported", "actual_cost": 1.0,
        "cost_currency": "USD", "cost_receipt_ref": "invoice-1",
    },
    {"credit_basis": "legacy_unverified", "actual_credits": 0.5},
    {"money_basis": "legacy_unverified", "actual_cost": 0.5},
])
def test_failed_attempt_with_charge_evidence_and_no_job_id_blocks_paid_replay(
    charge_evidence: dict[str, object],
) -> None:
    """An empty provider ID cannot erase a recorded charge or legacy uncertainty."""
    provider = CountingRealProvider()
    budget = RecordingBudget()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    with music_db_session() as session:
        insert_generation_plan(session, plan)
        for variant in variants:
            insert_creative_variant(session, variant)
        insert_generation_attempt(session, GenerationAttempt(
            attempt_id="failed-but-billing-recorded",
            generation_plan_id=plan.plan_id,
            creative_variant_id=variants[0].variant_id,
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            provider_job_id="",
            status=AttemptStatus.FAILED,
            request_hash="request", request_payload_hash="request",
            started_at=NOW, submitted_at=NOW, completed_at=NOW,
            **charge_evidence,
        ))
        guard = _live_guard(budget)
        result = GenerationEngine(
            clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
            write_guard=guard,
        ).execute_plan(
            session, plan=plan, variants=variants, spec=spec,
            provider_adapter=provider,
        )
        assert len(list_generation_attempts(session, plan.plan_id)) == 1
    assert result.status is PlanStatus.FAILED
    assert "provider_job_reconciliation_required" in result.errors
    assert provider.calls == 0 and budget.reserved == [] and guard.decisions == []


@pytest.mark.parametrize("legacy_job_id", ["", "   "])
def test_historical_success_candidate_without_provider_job_id_requires_reconciliation(
    legacy_job_id: str,
) -> None:
    """An old candidate is not evidence that a REAL provider job ID existed.

    The historical successful attempt and candidate are committed in a prior
    session. Even a fresh permissive guard cannot purchase another variant
    while that provenance is unresolved.
    """
    provider = CountingRealProvider()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    db = create_music_engine(static_pool=True)
    try:
        with music_session(db) as session:
            insert_generation_plan(session, plan)
            for variant in variants:
                insert_creative_variant(session, variant)
            old_id = "legacy_paid_success"
            insert_generation_attempt(session, GenerationAttempt(
                attempt_id=old_id,
                generation_plan_id=plan.plan_id,
                creative_variant_id=variants[0].variant_id,
                provider_id=provider.provider_id,
                model_id=provider.model_id,
                provider_job_id=legacy_job_id,
                status=AttemptStatus.SUCCEEDED,
                request_hash="historical", request_payload_hash="historical",
                started_at=NOW, submitted_at=NOW, completed_at=NOW,
                actual_credits=1.0, credit_basis="provider_reported",
                credit_receipt_ref="historical-credit-receipt",
            ))
            insert_candidate(session, GeneratedCandidate(
                candidate_id="legacy_candidate",
                source_attempt_id=old_id,
                track_spec_id=plan.track_spec_id,
                experiment_id=plan.experiment_id,
                generation_plan_id=plan.plan_id,
                status=CandidateStatus.GENERATED,
                provider_id=provider.provider_id,
                model_id=provider.model_id,
                created_at=NOW, updated_at=NOW,
            ))
            session.commit()

        budget = RecordingBudget()
        guard = _live_guard(budget)
        with music_session(db) as session:
            replay = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=guard,
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            assert len(list_generation_attempts(session, plan.plan_id)) == 1
            session.rollback()

        assert replay.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in replay.errors
        assert provider.calls == 0
        assert budget.reserved == [] and guard.decisions == []
    finally:
        db.dispose()


def test_unknown_real_submission_is_not_replayed_after_music_transaction_rolls_back() -> None:
    """The independent ops reservation prevents another paid call after a crash."""
    class AmbiguousRealProvider(CountingRealProvider):
        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.calls += 1
            raise RuntimeError("transport failed after possible provider acceptance")

    ops_engine = create_ops_engine()
    try:
        ledger = BudgetLedger(ops_session_factory(ops_engine), currency="USD")
        month_start = datetime(2026, 10, 1, tzinfo=UTC)
        ledger.set_ceiling(
            limit=Money.of("3", "USD"), period_start=month_start,
            period_end=datetime(2026, 11, 1, tzinfo=UTC),
            set_by="unit-test", now=NOW,
        )
        ledger.set_rate(
            capability=GENERATE_CAPABILITY, cost=Money.of("1", "USD"),
            effective_from=month_start, set_by="unit-test", now=NOW,
        )
        spec = adapt_track_spec(_spec())
        plan, variants = plan_generation(spec, created_at=NOW)
        provider = AmbiguousRealProvider()
        guard = _live_guard(PersistentBudgetGate(ledger, clock=FixedClock(NOW)))

        with (
            pytest.raises(RuntimeError, match="transport failed"),
            music_db_session() as session,
        ):
            GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=guard,
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
        assert provider.calls == 1
        assert len(ledger.entries()) == 1
        assert ledger.entries()[0].status.value == "reserved"

        # Simulate a process restart: the music transaction disappeared, but
        # the ops reservation survived. The original deterministic attempt ID
        # now replays the same reservation rather than creating a new charge.
        with music_db_session() as session:
            replay = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=_live_guard(
                    PersistentBudgetGate(ledger, clock=FixedClock(NOW))
                ),
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            assert list_generation_attempts(session, plan.plan_id) == []
        assert replay.status is PlanStatus.FAILED
        assert replay.stopped_reason == "budget_reservation_replayed"
        assert provider.calls == 1
        assert len(ledger.entries()) == 1
        assert ledger.entries()[0].status.value == "reserved"
    finally:
        ops_engine.dispose()


@pytest.mark.parametrize(("status", "provider_job_id"), [
    (AttemptStatus.SUBMITTED, ""),
    (AttemptStatus.PROCESSING, ""),
    (AttemptStatus.CANCELLED, ""),
    (AttemptStatus.SUCCEEDED, ""),
    (AttemptStatus.SUCCEEDED, "job-from-provider"),
    (AttemptStatus.FAILED, "job-from-provider"),
])
def test_live_attempt_with_unreconciled_outcome_needs_reconciliation(
    status: AttemptStatus, provider_job_id: str,
) -> None:
    """No persisted job ID is not proof that a paid submission was never accepted."""
    budget = RecordingBudget()
    provider = CountingRealProvider()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(
        clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
        write_guard=_live_guard(budget),
    )
    with music_db_session() as session:
        insert_generation_plan(session, plan)
        for variant in variants:
            insert_creative_variant(session, variant)
        insert_generation_attempt(session, GenerationAttempt(
            attempt_id="att-accepted-incomplete",
            generation_plan_id=plan.plan_id,
            creative_variant_id=variants[0].variant_id,
            provider_id=provider.provider_id, model_id=provider.model_id,
            provider_job_id=provider_job_id,
            status=status,
            request_hash="request", request_payload_hash="request",
            started_at=NOW, submitted_at=NOW,
            completed_at=NOW if status in {
                AttemptStatus.SUCCEEDED, AttemptStatus.FAILED, AttemptStatus.CANCELLED,
            } else None,
        ))
        result = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec,
            provider_adapter=provider,
        )
        assert len(list_generation_attempts(session, plan.plan_id)) == 1
        replay = engine.execute_plan(
            session, plan=plan, variants=variants, spec=spec,
            provider_adapter=provider,
        )
        assert len(list_generation_attempts(session, plan.plan_id)) == 1

    assert result.status is PlanStatus.FAILED
    assert replay.status is PlanStatus.FAILED
    assert "provider_job_reconciliation_required" in result.errors
    assert "provider_job_reconciliation_required" in replay.errors
    assert provider.calls == 0
    assert budget.reserved == []
    assert engine.write_guard.decisions == []


def test_committed_submitted_attempt_without_job_id_blocks_new_paid_call() -> None:
    """A new session/guard cannot interpret a persisted SUBMITTED row as no charge."""
    engine = create_music_engine(static_pool=True)
    try:
        spec = adapt_track_spec(_spec())
        plan, variants = plan_generation(spec, created_at=NOW)
        provider = CountingRealProvider()
        with music_session(engine) as session:
            insert_generation_plan(session, plan)
            for variant in variants:
                insert_creative_variant(session, variant)
            insert_generation_attempt(session, GenerationAttempt(
                attempt_id="submitted-without-provider-receipt",
                generation_plan_id=plan.plan_id,
                creative_variant_id=variants[0].variant_id,
                provider_id=provider.provider_id, model_id=provider.model_id,
                provider_job_id="", status=AttemptStatus.SUBMITTED,
                request_hash="submitted", request_payload_hash="submitted",
                started_at=NOW, submitted_at=NOW,
            ))
            session.commit()

        # Simulate another process loading the durable music data with a
        # newly constructed guard that otherwise funds all paid calls.
        budget = RecordingBudget()
        guard = _live_guard(budget)
        with music_session(engine) as session:
            result = GenerationEngine(
                clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                write_guard=guard,
            ).execute_plan(
                session, plan=plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )
            assert len(list_generation_attempts(session, plan.plan_id)) == 1
            session.rollback()

        assert result.status is PlanStatus.FAILED
        assert "provider_job_reconciliation_required" in result.errors
        assert provider.calls == 0
        assert budget.reserved == [] and guard.decisions == []
    finally:
        engine.dispose()


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


def test_engine_refuses_stale_plan_budget_and_variant_before_real_write() -> None:
    """A direct engine caller cannot swap a persisted budget or creative treatment."""
    provider = CountingRealProvider()
    budget = RecordingBudget()
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(
        clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
        write_guard=_live_guard(budget),
    )
    with music_db_session() as session:
        insert_generation_plan(session, plan)
        for original_variant in variants:
            insert_creative_variant(session, original_variant)

        altered_plan = plan.model_copy(update={"credit_limit": plan.credit_limit + 100.0})
        with pytest.raises(GenerationPlanSpecMismatch, match="persisted generation plan"):
            engine.execute_plan(
                session, plan=altered_plan, variants=variants, spec=spec,
                provider_adapter=provider,
            )

        with pytest.raises(GenerationPlanSpecMismatch, match="creative variant set"):
            engine.execute_plan(
                session, plan=plan, variants=variants[:1], spec=spec,
                provider_adapter=provider,
            )

        modified = variants[0].model_copy(update={"prompt_delta": {"extra_instruction": "swap"}})
        with pytest.raises(GenerationPlanSpecMismatch, match="creative variant"):
            engine.execute_plan(
                session, plan=plan, variants=[modified, *variants[1:]], spec=spec,
                provider_adapter=provider,
            )

        changed_wire = {
            **spec.raw_spec,
            "bounded_preferences": {**spec.raw_spec["bounded_preferences"], "bpm": 91},
        }
        changed_spec = adapt_track_spec(changed_wire)
        with pytest.raises(GenerationPlanSpecMismatch, match="C3 track spec"):
            engine.execute_plan(
                session, plan=plan, variants=variants, spec=changed_spec,
                provider_adapter=provider,
            )

        assert get_generation_plan(session, plan.plan_id).status is PlanStatus.PLANNED
        assert list_generation_attempts(session, plan.plan_id) == []
    assert provider.calls == 0 and budget.reserved == []


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


def test_api_rejects_disallowed_real_provider_with_guard_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P4 cannot spend under a real provider excluded by P3's C3 brief."""
    def unreachable(self: SunoAdapter, request: GenerationRequest) -> GenerationResponse:
        raise AssertionError("disallowed real provider must never be called")

    monkeypatch.setattr(SunoAdapter, "generate", unreachable)
    client = _client(default_provider="suno", guard=_live_guard(RecordingBudget()))
    spec = _spec(provider_policy={"allowed_providers": ["mock"]})
    plan = client.post("/music/plans", json=spec, headers=HEADERS)
    assert plan.status_code == 200
    plan_id = plan.json()["plan_id"]
    refused = client.post(
        f"/music/plans/{plan_id}/execute", json=spec, headers=HEADERS,
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "external_write_deny"
    assert refused.json()["detail"]["reason"] == "plan_provider_not_allowed"
    persisted = client.get(f"/music/plans/{plan_id}", headers=HEADERS)
    assert persisted.status_code == 200
    assert persisted.json()["plan"]["status"] == "failed"


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



def test_provider_mode_music_real_selects_suno_and_nothing_else_does() -> None:
    assert default_provider_for(ExecutionSettings()) == "mock"
    assert default_provider_for(
        ExecutionSettings(provider_modes={"music": ProviderMode.REAL})) == "suno"
    assert default_provider_for(
        ExecutionSettings(provider_modes={"youtube": ProviderMode.REAL})) == "mock"


@pytest.mark.parametrize(("granted", "reason"), [
    (False, "no_authorization"),
    (True, "budget_ledger_unavailable"),
])
def test_service_guard_reads_the_owner_grants_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, granted: bool, reason: str,
) -> None:
    """Without an injected guard the service loads ``PLM_WRITE_AUTHORIZATIONS_FILE``: a grant
    moves the refusal from the missing authorization on to the (absent) budget ledger."""
    def unreachable(self: SunoAdapter, request: GenerationRequest) -> GenerationResponse:
        raise AssertionError("the real provider must not be called")

    monkeypatch.setattr(SunoAdapter, "generate", unreachable)
    grants = tmp_path / "grants.json"
    grants.write_text(json.dumps([{
        "capability": "music.generate", "granted_by": "owner",
        "granted_at": "2026-10-15T00:00:00+00:00", "expires_at": "2026-10-16T00:00:00+00:00",
    }] if granted else []), encoding="utf-8")
    monkeypatch.setenv("PLM_WRITE_AUTHORIZATIONS_FILE", str(grants))

    from services.music_factory.storage.artifacts import MinioArtifactStorage
    from tests.support.fake_minio import FakeMinio

    platform = load_settings(env=dict(ENV))
    engine = create_music_engine(static_pool=True)
    app = create_app(
        session_factory=lambda: music_session(engine),
        settings=load_music_factory_settings(platform=platform, default_provider="suno"),
        storage=MinioArtifactStorage(platform, client=FakeMinio()),  # type: ignore[arg-type]
        execution=ExecutionSettings(provider_modes={"music": ProviderMode.REAL},
                                    policies={"music.generate": ExecutionPolicy.LIVE}),
        clock=FixedClock(NOW),
    )
    client = TestClient(app)
    plan = client.post("/music/plans", json=_spec(), headers=HEADERS).json()
    resp = client.post(f"/music/plans/{plan['plan_id']}/execute", json=_spec(), headers=HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == reason

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

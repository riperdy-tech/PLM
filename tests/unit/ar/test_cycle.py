# tests/unit/ar/test_cycle.py
"""P2.10 end-to-end: §29's allocation cycle, its persistence, replay and the ceiling.

The cycle is the producer of everything P2 ships: decisions, directives and the
version strings that bind each value to the constants that produced it (§76).
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from plm_contracts import AllocationType

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.decisioning.cycle import (
    CycleRequest,
    MarketSnapshot,
    PolicyNotConfigured,
    run_allocation_cycle,
    seed_for_cycle,
)
from services.autonomous_ar.decisioning.portfolio import build_directives
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.decision import ArmSample
from services.autonomous_ar.domain.enums import (
    AllocationCycleStatus,
    ArmLifecycleStatus,
    ReasonCode,
)
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PosteriorState
from services.autonomous_ar.policy.constraints import (
    BudgetCeilingExceeded,
    Portfolio,
    Selection,
    directive_budget_share,
)
from services.autonomous_ar.storage import repositories
from services.autonomous_ar.storage.models import ar_allocation_cycle
from tests.support.ar_db import ar_session, create_ar_engine

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_ar_settings()
FULL = {name: 60.0 for name in
        ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
         "cross_platform", "production_fit")}


def _arm(arm_id: str, *, score: float, components: dict[str, float | None] | None = None,
         freshness: float = 0.0) -> ContentArm:
    return ContentArm(
        arm_id=arm_id,
        dimensions=ArmDimensions(genre=arm_id.split("-")[0], use_cases=(f"uc-{arm_id}",),
                                 language=f"lang-{arm_id}", vocal_mode="vocal"),
        p1_score=score, p1_confidence=0.7, component_scores=components or dict(FULL),
        observation_count=5, source_count=2, as_of=NOW, score_version="p1-v1",
        freshness_seconds=freshness,
    )


def _snapshot(*arms: ContentArm, source: str = "live") -> MarketSnapshot:
    return MarketSnapshot(arms=arms, as_of=NOW, source=source, score_version="p1-v1")


def _request(**overrides: object) -> CycleRequest:
    base: dict[str, object] = {"brand_id": "neon_seoul", "as_of": NOW, "slot_count": 4,
                               "budget": 8.0, "policy_version": "p2-v1"}
    base.update(overrides)
    return CycleRequest(**base)  # type: ignore[arg-type]


def _seed_policy(session: object) -> None:
    repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul", created_at=NOW)


def test_full_cycle_persists_decisions_directives_and_every_version() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        result = run_allocation_cycle(
            session, _request(), settings=SETTINGS,
            market=_snapshot(_arm("rnb-1", score=82.0), _arm("citypop-2", score=78.0),
                             _arm("house-3", score=61.0), _arm("jazz-4", score=55.0)),
            now=NOW,
        )
        assert result.replay is False
        assert result.status is AllocationCycleStatus.COMPLETED
        assert len(result.directives) == 4
        assert len({d.arm_id for d in result.directives}) == 4
        decisions = repositories.load_decisions(session, result.cycle_id)
        assert {row["policy_version"] for row in decisions} == {"p2-v1"}
        assert {row["model_version"] for row in decisions} == {"p2-nn-v1"}
        assert {row["feature_version"] for row in decisions} == {"p2-features-v1"}
        assert {row["normalization_version"] for row in decisions} == {"p2-proxy-norm-v1"}
        assert {row["p1_score_version"] for row in decisions} == {"p1-v1"}
        # SHADOW (the settings default): computed and auditable, never actionable
        assert repositories.list_queued_directives(
            session, brand_id="neon_seoul", activation_states=("AUTONOMOUS",)) == []
        for directive in result.directives:
            assert directive.expires_at == NOW + timedelta(
                hours=DEFAULT_POLICY.directive_ttl_hours)
            assert directive.budget_limit == 8.0 / 4
            # ADR-0023 #2: the fraction is reported per allocation class, never consulted
            expected = {
                AllocationType.EXPLOIT: DEFAULT_POLICY.exploit_fraction,
                AllocationType.ADJACENT_EXPLORE: DEFAULT_POLICY.adjacent_explore_fraction,
                AllocationType.RADICAL_EXPLORE: DEFAULT_POLICY.radical_explore_fraction,
            }[directive.allocation_type]
            assert directive.exploration_fraction == expected
        assert sum(d.budget_limit for d in result.directives) <= 8.0 + 1e-9
        stored = repositories.load_cycle(session, result.cycle_id)
        assert stored is not None
        assert stored["status"] == AllocationCycleStatus.COMPLETED.value
        assert stored["activation_state"] == "SHADOW"
        assert stored["random_seed"] == seed_for_cycle(result.cycle_id), (
            "§67: the seed is persisted, so a production portfolio is exactly reproducible"
        )


def test_replaying_the_same_cycle_is_idempotent() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        market = _snapshot(_arm("rnb-1", score=82.0), _arm("citypop-2", score=78.0))
        first = run_allocation_cycle(session, _request(slot_count=2), settings=SETTINGS,
                                     market=market, now=NOW)
        second = run_allocation_cycle(session, _request(slot_count=2), settings=SETTINGS,
                                      market=market, now=NOW)
        assert second.replay is True and second.cycle_id == first.cycle_id
        assert [d.directive_id for d in second.directives] == [d.directive_id
                                                               for d in first.directives]
        count = session.execute(sa.select(sa.func.count()).select_from(ar_allocation_cycle)) \
            .scalar_one()
        assert count == 1, "a replay writes no second cycle"
        assert len(repositories.load_directives_for_cycle(session, first.cycle_id)) == 2
        assert len(repositories.load_decisions(session, first.cycle_id)) == 2


def test_under_covered_arm_is_flagged_not_ranked() -> None:
    engine = create_ar_engine()
    thin = {name: (42.0 if name == "demand" else None) for name in FULL}
    with ar_session(engine) as session:
        _seed_policy(session)
        result = run_allocation_cycle(
            session, _request(slot_count=2), settings=SETTINGS,
            market=_snapshot(_arm("rnb-1", score=82.0),
                             _arm("thin-2", score=99.0, components=thin)), now=NOW,
        )
        assert "thin-2" not in {d.arm_id for d in result.directives}
        assert any(rejection.arm_id == "thin-2"
                   and rejection.reason is ReasonCode.EVIDENCE_INSUFFICIENT
                   for rejection in result.rejected)
        # ADR-0023 #5: the flag is persisted on the arm's decision row, never silent
        flagged = next(row for row in repositories.load_decisions(session, result.cycle_id)
                       if row["arm_id"] == "thin-2")
        assert flagged["reason_codes"] == ["EVIDENCE_INSUFFICIENT"]
        assert flagged["constraint_adjustments_json"] == {
            "ineligible": "EVIDENCE_INSUFFICIENT"}
        assert flagged["rank_before_constraints"] is None
        assert flagged["rank_after_constraints"] is None
        assert result.status is AllocationCycleStatus.PARTIAL


def test_stale_arm_is_rejected_and_a_market_score_shift_is_flagged() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        run_allocation_cycle(session, _request(slot_count=1), settings=SETTINGS,
                             market=_snapshot(_arm("rnb-1", score=90.0)), now=NOW)
        later = NOW + timedelta(days=8)
        second = run_allocation_cycle(
            session, _request(as_of=later, slot_count=1), settings=SETTINGS,
            market=MarketSnapshot(arms=(_arm("rnb-1", score=40.0, freshness=200 * 3600.0),
                                        _arm("rnb-1b", score=40.0, freshness=200 * 3600.0)),
                                  as_of=later, source="live", score_version="p1-v1"),
            now=later,
        )
        assert any(rejection.reason is ReasonCode.MARKET_STALE
                   for rejection in second.rejected), "§83: stale data is never used as current"
        assert ReasonCode.MARKET_REGIME_CHANGE_SUSPECTED.value in second.warnings


def test_unknown_policy_refuses_the_cycle() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session, pytest.raises(PolicyNotConfigured, match="p2-v1"):
        run_allocation_cycle(session, _request(), settings=SETTINGS,
                             market=_snapshot(_arm("rnb-1", score=80.0)), now=NOW)


def test_seed_is_derived_from_the_cycle_id_and_is_stable() -> None:
    from services.autonomous_ar.storage.ids import deterministic_id

    cycle_id = deterministic_id("cycle", "neon_seoul", NOW.isoformat(), "p2-v1")
    assert seed_for_cycle(cycle_id) == seed_for_cycle(cycle_id)
    assert 0 <= seed_for_cycle(cycle_id) < 2**31
    assert seed_for_cycle(cycle_id) != seed_for_cycle(
        deterministic_id("cycle", "neon_seoul", NOW.isoformat(), "p2-v2"))


def test_autonomous_cycles_serve_directives_while_shadow_cycles_do_not() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        run_allocation_cycle(session, _request(slot_count=1), settings=SETTINGS,
                             market=_snapshot(_arm("rnb-1", score=80.0)), now=NOW)
        assert repositories.list_queued_directives(
            session, brand_id="neon_seoul", activation_states=("AUTONOMOUS",)) == []
        repositories.set_channel_state(session, brand_id="neon_seoul",
                                       activation_state="AUTONOMOUS", now=NOW,
                                       reason="operator go", updated_by="operator")
        run_allocation_cycle(
            session, _request(slot_count=1, as_of=NOW + timedelta(days=1)), settings=SETTINGS,
            market=_snapshot(_arm("rnb-1", score=80.0)), now=NOW + timedelta(days=1))
        served = repositories.list_queued_directives(
            session, brand_id="neon_seoul", activation_states=("AUTONOMOUS",))
        assert len(served) == 1, "ADR-0024: only AUTONOMOUS cycles are actionable"


def test_budget_ceiling_boundary_refuses_overspend_but_never_crosses_it() -> None:
    # (a) the arithmetic: an equal share, and a refusal when the budget or slots are absurd
    assert directive_budget_share(cycle_budget=8.0, slot_count=4) == 2.0
    with pytest.raises(ValueError, match="budget"):
        directive_budget_share(cycle_budget=0.0, slot_count=4)
    with pytest.raises(ValueError, match="slot_count"):
        directive_budget_share(cycle_budget=8.0, slot_count=0)

    # (b) the ceiling can be filled exactly: three shares of 2.0 against a 6.0 budget
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        result = run_allocation_cycle(
            session, _request(slot_count=3, budget=6.0), settings=SETTINGS,
            market=_snapshot(_arm("rnb-1", score=82.0), _arm("citypop-2", score=78.0),
                             _arm("house-3", score=61.0)), now=NOW,
        )
        shares = [d.budget_limit for d in result.directives]
        assert shares == [2.0, 2.0, 2.0]
        assert sum(shares) == 6.0, "at the ceiling is allowed; above it is not"

    # (c) the refusal itself: a portfolio whose shares would cross the ceiling is refused,
    # naming the arithmetic — this is the tripwire ADR-0023 #1 asks for, proven to fire.
    arms = {f"arm-{index}": _arm(f"arm-{index}", score=50.0 + index) for index in range(3)}
    posteriors = {
        arm_id: PosteriorState(
            arm_id=arm_id, model_type="normal-normal", model_version="p2-nn-v1",
            prior_mean=0.5, prior_variance=0.05, posterior_mean=0.5,
            posterior_variance=0.05, observation_count=0, effective_observation_count=0.0,
            update_version=0, last_update_at=NOW,
        )
        for arm_id in arms
    }
    selections = tuple(
        Selection(arm_id=arm_id, allocation_type=AllocationType.RADICAL_EXPLORE,
                  sample=ArmSample(arm_id=arm_id, sampled_reward=0.5,
                                   posterior_expected_reward=0.5),
                  reason_codes=("RADICAL_EXPLORATION",))
        for arm_id in sorted(arms)
    )
    portfolio = Portfolio(selections=selections, rejections=(), warnings=())
    with pytest.raises(BudgetCeilingExceeded, match="9.0"):
        build_directives(portfolio, cycle_id="cycle-1", policy=DEFAULT_POLICY,
                         posteriors=posteriors, arms=arms, settings=SETTINGS,
                         expires_at=NOW, budget_share=3.0, cycle_budget=6.0)


def test_the_same_request_in_two_fresh_stores_produces_the_identical_portfolio() -> None:
    """§67's replayability: the seed derives from the cycle id, so two stores agree."""
    market = _snapshot(_arm("rnb-1", score=82.0), _arm("citypop-2", score=78.0),
                       _arm("house-3", score=61.0))
    portfolios: list[list[dict[str, object]]] = []
    for _ in range(2):
        engine = create_ar_engine()
        with ar_session(engine) as session:
            _seed_policy(session)
            result = run_allocation_cycle(session, _request(slot_count=3), settings=SETTINGS,
                                          market=market, now=NOW)
            portfolios.append([directive.to_wire() for directive in result.directives])
    assert portfolios[0] == portfolios[1], "same inputs, different store, identical portfolio"
    first = portfolios[0][0]
    assert first["directive_version"] == SETTINGS.directive_version == "p2-directive-v1"
    assert first["policy_version"] == SETTINGS.policy_version == "p2-v1"
    assert first["value_model"] == SETTINGS.value_model == "early-stage-proxy-v1"
    assert len(first) == 17, "the frozen C2 field count, produced by the real cycle"


def test_retirement_needs_mature_evidence_and_reopens_on_a_market_rise() -> None:
    from services.autonomous_ar.domain.posterior import PosteriorState
    from services.autonomous_ar.policy.lifecycle import evaluate_retirements, reopen_reason

    weak = PosteriorState(
        arm_id="rnb-1", model_type="normal-normal", model_version="p2-nn-v1",
        prior_mean=0.5, prior_variance=0.05, posterior_mean=0.10, posterior_variance=0.01,
        observation_count=6, effective_observation_count=6.0, update_version=6,
        last_update_at=NOW,
    )
    fresh = replace(weak, observation_count=1, effective_observation_count=1.0)
    retirements = evaluate_retirements({"rnb-1": weak}, mature_counts={"rnb-1": 5},
                                       policy=DEFAULT_POLICY, now=NOW)
    assert [decision.arm_id for decision in retirements] == ["rnb-1"], (
        "§38: five mature observations and a weak posterior retire; one never does"
    )
    assert evaluate_retirements({"rnb-1": fresh}, mature_counts={"rnb-1": 1},
                                policy=DEFAULT_POLICY, now=NOW) == ()
    recovered, stable = _arm("rnb-1", score=75.0), _arm("rnb-1", score=45.0)
    retired = ArmLifecycleStatus.RETIRED
    assert reopen_reason(recovered, stored_score=40.0, policy=DEFAULT_POLICY,
                         state=retired) == "market_score_recovered"
    assert reopen_reason(stable, stored_score=40.0, policy=DEFAULT_POLICY,
                         state=retired) is None
    assert reopen_reason(recovered, stored_score=40.0, policy=DEFAULT_POLICY,
                         state=ArmLifecycleStatus.ACTIVE) is None

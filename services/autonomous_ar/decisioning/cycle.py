# services/autonomous_ar/decisioning/cycle.py
"""§29's fifteen steps, persisted; the only place a cycle's rows are written.

Replay is structural: the cycle id derives from (brand, as_of, policy version), every id
derives from identity, the seed is derived or supplied and persisted — a re-run of the
same request returns the stored cycle instead of writing a second one.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from plm_contracts import AllocationType, ChannelActivationState

from ..config import ARSettings
from ..domain.arm import ContentArm
from ..domain.decision import ConstraintRejection
from ..domain.directive import ProductionDirective
from ..domain.enums import (
    AllocationCycleStatus,
    ArmLifecycleStatus,
    DirectiveStatus,
    ReasonCode,
)
from ..domain.posterior import PosteriorState
from ..modeling.decay import detect_regime_shift
from ..modeling.prior import cold_start_prior
from ..modeling.reward_model import NormalNormalRewardModel, RewardModel
from ..observability.logging import emit_alert, evaluate_cycle_alerts, log_cycle
from ..observability.metrics import CONSTRAINT_REJECTIONS, CYCLE_FAILURES, record_cycle
from ..policy.constraints import directive_budget_share
from ..policy.exploration import effective_fractions, quota_from_fractions
from ..policy.lifecycle import evaluate_retirements, reopen_reason
from ..storage import repositories
from ..storage.ids import deterministic_id
from .planner import prepare_cycle
from .portfolio import build_directives, build_portfolio

__all__ = ["CycleRequest", "CycleResult", "MarketSnapshot", "PolicyNotConfigured",
           "run_allocation_cycle", "seed_for_cycle"]


class PolicyNotConfigured(RuntimeError):
    """§102: no validated policy row — refuse the cycle, never allocate on defaults."""


@dataclass(frozen=True, slots=True)
class CycleRequest:
    brand_id: str
    as_of: datetime
    slot_count: int
    budget: float
    policy_version: str
    activation_state: ChannelActivationState | None = None
    random_seed: int | None = None
    unavailable_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    arms: tuple[ContentArm, ...]
    as_of: datetime
    source: str          # "live" | "stored"
    score_version: str


@dataclass(frozen=True, slots=True)
class CycleResult:
    cycle_id: str
    status: AllocationCycleStatus
    directives: tuple[ProductionDirective, ...]
    rejected: tuple[ConstraintRejection, ...]
    warnings: tuple[str, ...]
    replay: bool


def seed_for_cycle(cycle_id: str) -> int:
    """Deterministic 31-bit seed from the cycle id; persisted on the cycle row (§67)."""
    return int(UUID(cycle_id).hex[:16], 16) % (2**31)


def _row_to_directive(row: Any) -> ProductionDirective:
    return ProductionDirective(
        directive_id=str(row["id"]), decision_id=str(row["decision_id"]),
        arm_id=row["arm_id"], allocation_type=row["allocation_type"],
        priority=row["priority"], quantity=row["quantity"], brief=row["brief_json"],
        expected_value=row["expected_value"], uncertainty=row["uncertainty"],
        budget_limit=row["budget_limit"], market_context=row["market_context_json"],
        selection_reason=tuple(row["selection_reason_json"]),
        directive_version=row["directive_version"], policy_version=row["policy_version"],
        expires_at=row["expires_at"], exploration_fraction=row["exploration_fraction"],
        value_model=row["value_model"],
    )


def run_allocation_cycle(session: Any, request: CycleRequest, *, settings: ARSettings,
                         market: MarketSnapshot, model: RewardModel | None = None,
                         now: datetime) -> CycleResult:
    """§29, in order. Every step below is one of the spec's fifteen.

    A cycle that cannot finish increments `CYCLE_FAILURES` and re-raises — a refusal is
    counted, never swallowed. A replay is not a failure: it returns the stored rows.
    """
    try:
        return _run_cycle(session, request, settings=settings, market=market, model=model,
                          now=now)
    except Exception:
        CYCLE_FAILURES.inc()
        raise


def _run_cycle(session: Any, request: CycleRequest, *, settings: ARSettings,
               market: MarketSnapshot, model: RewardModel | None = None,
               now: datetime) -> CycleResult:
    """The body of `run_allocation_cycle` (split out so the failure counter can wrap it)."""
    started = time.perf_counter()
    resolved_model = model or NormalNormalRewardModel(
        observation_variance=settings.observation_variance,
        variance_floor=settings.posterior_variance_floor,
    )
    # 0 — the policy must exist (§102); no silent default
    policy = repositories.load_policy(session, request.policy_version)
    if policy is None:
        raise PolicyNotConfigured(
            f"no policy row for version {request.policy_version!r}: insert and validate a "
            "policy before running a cycle (§49/§92/§102)"
        )
    cycle_id = deterministic_id("cycle", request.brand_id, request.as_of.isoformat(),
                                request.policy_version)
    seed = request.random_seed if request.random_seed is not None else seed_for_cycle(cycle_id)
    activation = request.activation_state or ChannelActivationState(
        repositories.get_channel_state(session, brand_id=request.brand_id,
                                       default=settings.default_activation_state.value))
    policy_id = repositories.insert_policy(session, policy, brand_id=request.brand_id,
                                           created_at=now)
    if not repositories.insert_cycle(
        session, cycle_id=cycle_id, brand_id=request.brand_id, started_at=now,
        status=AllocationCycleStatus.RUNNING, slot_count=request.slot_count,
        budget=request.budget, policy_id=policy_id, p1_snapshot_time=market.as_of,
        activation_state=activation.value, random_seed=seed,
    ):  # replay: return the stored cycle, write nothing
        cycle = repositories.load_cycle(session, cycle_id)
        if cycle is None:  # pragma: no cover - insert_cycle said the row exists
            raise RuntimeError(f"cycle {cycle_id!r} vanished between insert and load")
        rows = repositories.load_directives_for_cycle(session, cycle_id)
        return CycleResult(cycle_id=cycle_id,
                           status=AllocationCycleStatus(cycle["status"]),
                           directives=tuple(_row_to_directive(row) for row in rows),
                           rejected=(), warnings=tuple(cycle["warning_codes_json"]),
                           replay=True)

    warnings: list[str] = []
    if market.source == "stored":
        warnings.append(ReasonCode.MARKET_UNAVAILABLE.value)   # §102 degraded, explicit
    if request.unavailable_capabilities:
        warnings.append(ReasonCode.PROVIDER_CAPABILITY_UNAVAILABLE.value)  # §82, see open items

    arms: dict[str, ContentArm] = {arm.arm_id: arm for arm in market.arms}
    # 1 — load eligible P1 arms (they arrive as a snapshot); 2 — refresh arm states
    for arm in market.arms:
        repositories.upsert_arm_state(session, brand_id=request.brand_id, arm_id=arm.arm_id,
                                      now=now)
    # 3 — recent experiment history is folded into the posteriors' effective counts
    # 4 — load posterior state; cold start from P1 evidence when none exists (§10)
    posteriors: dict[str, PosteriorState] = {}
    for arm in market.arms:
        existing = repositories.load_posterior(session, arm.arm_id)
        if existing is None:
            prior = cold_start_prior(arm, settings=settings, policy=policy)
            existing = replace(
                resolved_model.initialize_prior(arm, prior),
                # Version binding: the prior transform's version and the cycle's value
                # model ride on the state they produced, so the posterior row alone
                # identifies its provenance (§76).
                context_state_json={"prior_version": prior.prior_version,
                                    "value_model": policy.value_model},
            )
            repositories.upsert_posterior(session, existing, brand_id=request.brand_id,
                                          expected_version=0)
        posteriors[arm.arm_id] = existing
    # 5 — freshness gate; 6 — blocked/policy constraints; 7 — cooldown/fatigue
    state_rows = {row["arm_id"]: row for row in repositories.load_arm_states(session)}
    states = {arm_id: ArmLifecycleStatus(row["status"]) for arm_id, row in state_rows.items()}
    # 5b — §38 retirement and §39 reopening, decided before eligibility is computed
    mature_counts = {arm_id: int(row["mature_result_count"])
                     for arm_id, row in state_rows.items()}
    retirement_decisions = evaluate_retirements(posteriors, mature_counts=mature_counts,
                                                policy=policy, now=now)
    for decision in retirement_decisions:
        repositories.set_arm_status(session, arm_id=decision.arm_id,
                                    status=ArmLifecycleStatus.RETIRED, now=now,
                                    reason=decision.reason)
        states[decision.arm_id] = ArmLifecycleStatus.RETIRED
        warnings.append(ReasonCode.WEAK_POSTERIOR.value)  # §38 evidence, never silent
    retirements = len(retirement_decisions)
    reactivations = 0
    for arm in market.arms:
        if states.get(arm.arm_id) is not ArmLifecycleStatus.RETIRED:
            continue
        previous = repositories.load_latest_feature_snapshot(session, arm.arm_id)
        if previous is None or previous["p1_score"] is None:
            continue  # §39 needs a stored score to compare against; none means no reopen
        reason = reopen_reason(arm, stored_score=float(previous["p1_score"]),
                               policy=policy, state=states[arm.arm_id])
        if reason is not None:
            repositories.set_arm_status(session, arm_id=arm.arm_id,
                                        status=ArmLifecycleStatus.ACTIVE, now=now,
                                        reason=reason)
            states[arm.arm_id] = ArmLifecycleStatus.ACTIVE
            reactivations += 1
    eligible_ids = {arm_id for arm_id, status in states.items()
                    if status in (ArmLifecycleStatus.CANDIDATE, ArmLifecycleStatus.ACTIVE)}
    selection_recency: dict[str, float] = {}
    for arm_id in states:
        if repositories.is_in_cooldown_state(session, arm_id=arm_id, now=now):
            eligible_ids.discard(arm_id)
        selection_recency[arm_id] = repositories.load_exposure_score(session, arm_id)
    snapshot_arms = tuple(arm for arm in market.arms if arm.arm_id in eligible_ids)
    regime_shift = False
    for arm in snapshot_arms:
        previous = repositories.load_latest_feature_snapshot(session, arm.arm_id)
        if previous is not None:
            regime_shift = regime_shift or bool(detect_regime_shift(
                arm.p1_score, float(previous["p1_score"]),
                relative_threshold=policy.nonstationarity.regime_relative_threshold))
    low_confidence = all(posteriors[arm.arm_id].observation_count == 0
                         for arm in snapshot_arms)
    if regime_shift:
        warnings.append(ReasonCode.MARKET_REGIME_CHANGE_SUSPECTED.value)
    fractions = effective_fractions(policy, low_confidence=low_confidence,
                                    regime_shift=regime_shift)
    quota = quota_from_fractions(request.slot_count, fractions)
    # 8 — contextual features snapshotted before any decision is written (no leakage)
    snapshot_ids = {arm.arm_id: repositories.insert_feature_snapshot(
        session, brand_id=request.brand_id, arm=arm, captured_at=market.as_of,
        feature_version=settings.feature_version) for arm in snapshot_arms}
    # 9 — sample posterior reward (seeded); 10/11 — constraints and budget by the walk
    prepared = prepare_cycle(arms=snapshot_arms, posteriors=posteriors, policy=policy,
                             settings=settings, model=resolved_model,
                             rng=random.Random(seed),
                             selection_recency=selection_recency,
                             min_components=policy.min_measured_components,
                             warnings=warnings)
    baselines = repositories.load_baselines(session, brand_id=request.brand_id)
    portfolio = build_portfolio(prepared, policy=policy, slot_count=request.slot_count,
                                quota=quota, arms=arms, states=states, baselines=baselines)
    share = directive_budget_share(request.budget, request.slot_count)
    # 12/14 — build the portfolio and its directives (frozen C2 payload)
    directives = build_directives(
        portfolio, cycle_id=cycle_id, policy=policy, posteriors=posteriors, arms=arms,
        settings=settings,
        expires_at=request.as_of + timedelta(hours=policy.directive_ttl_hours),
        budget_share=share, cycle_budget=request.budget,
    )
    # 13 — decision records: flagged ineligible arms, then every ranked arm
    for arm_id, reason in prepared.ineligible.items():
        repositories.insert_decision(
            session, cycle_id=cycle_id, arm_id=arm_id, allocation_type="exploit",
            sampled_reward=0.0, posterior_expected_reward=0.0, selection_priority=0.0,
            rank_before_constraints=None, rank_after_constraints=None,
            constraint_adjustments={"ineligible": reason}, reason_codes=(reason,),
            policy_version=policy.version, model_version=settings.model_version,
            feature_version=settings.feature_version,
            normalization_version=settings.proxy_normalization_version,
            p1_score_version=arms[arm_id].score_version if arm_id in arms else None,
            feature_snapshot_id=snapshot_ids.get(arm_id) or deterministic_id(
                "no_snapshot", cycle_id, arm_id),
            created_at=now,
        )
    selected_ids = {selection.arm_id for selection in portfolio.selections}
    for rank, sample in enumerate(prepared.ranked, start=1):
        allocation = prepared.classes[sample.arm_id]
        repositories.insert_decision(
            session, cycle_id=cycle_id, arm_id=sample.arm_id,
            allocation_type=allocation.value, sampled_reward=sample.sampled_reward,
            posterior_expected_reward=sample.posterior_expected_reward,
            selection_priority=sample.sampled_reward, rank_before_constraints=rank,
            rank_after_constraints=rank if sample.arm_id in selected_ids else None,
            constraint_adjustments={"assumed_cost": policy.assumed_cost_per_directive},
            reason_codes=(allocation.value.upper(),), policy_version=policy.version,
            model_version=settings.model_version, feature_version=settings.feature_version,
            normalization_version=settings.proxy_normalization_version,
            p1_score_version=arms[sample.arm_id].score_version,
            feature_snapshot_id=snapshot_ids[sample.arm_id], created_at=now,
        )
    for directive in directives:
        repositories.insert_directive(session, directive, brand_id=request.brand_id,
                                      status=DirectiveStatus.QUEUED, created_at=now)
        repositories.record_selection(
            session, arm_id=directive.arm_id, now=now, cooldown_until=None,
            recent_exposure_score=selection_recency.get(directive.arm_id, 0.0) + 1.0,
        )
    status = (AllocationCycleStatus.PARTIAL
              if portfolio.is_partial(request.slot_count) or prepared.ineligible
              else AllocationCycleStatus.COMPLETED)
    unique_warnings = list(dict.fromkeys(warnings))
    repositories.finish_cycle(
        session, cycle_id=cycle_id, status=status, finished_at=now,
        decision_count=len(prepared.ranked) + len(prepared.ineligible),
        selected_count=len(directives), rejected_count=len(portfolio.rejections),
        warning_codes=unique_warnings,
    )
    rejected: list[ConstraintRejection] = list(portfolio.rejections)
    for arm_id, reason in prepared.ineligible.items():
        try:
            rejected.append(ConstraintRejection(arm_id=arm_id, reason=ReasonCode(reason),
                                                detail="ineligible before ranking"))
        except ValueError:
            continue  # NO_POSTERIOR is an internal guard, not a contract reason token
    for rejection in portfolio.rejections:  # §87: one count per refusal reason
        CONSTRAINT_REJECTIONS.labels(reason=rejection.reason.value).inc()
    result = CycleResult(cycle_id=cycle_id, status=status, directives=directives,
                         rejected=tuple(rejected), warnings=tuple(unique_warnings), replay=False)
    # §87/§88/§94, after `finish_cycle`: counters, one structured line, then the alert codes
    runtime_ms = (time.perf_counter() - started) * 1000.0
    record_cycle(status=status.value, eligible=len(snapshot_arms), selected=len(directives),
                 rejected=len(portfolio.rejections), retirements=retirements,
                 reactivations=reactivations)
    log_cycle(result, brand_id=request.brand_id, policy_version=policy.version,
              p1_snapshot_time=market.as_of, runtime_ms=runtime_ms)
    stale_count = sum(1 for reason in prepared.ineligible.values()
                      if reason == ReasonCode.MARKET_STALE.value)
    selections = tuple((directive.arm_id,
                        prepared.clusters.get(directive.arm_id, directive.arm_id))
                       for directive in directives)
    realized_exploration = (sum(1 for directive in directives
                                if directive.allocation_type is not AllocationType.EXPLOIT)
                            / len(directives)) if directives else 0.0
    for code in evaluate_cycle_alerts(eligible=len(snapshot_arms), stale=stale_count,
                                      selections=selections,
                                      exploration_fraction=realized_exploration,
                                      policy=policy):
        emit_alert(code, brand_id=request.brand_id,
                   detail=(f"cycle {cycle_id}: {len(directives)} selected of "
                           f"{len(snapshot_arms)} eligible"))
    return result

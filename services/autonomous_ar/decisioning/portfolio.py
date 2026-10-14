# services/autonomous_ar/decisioning/portfolio.py
"""§29 steps 10–12: the constraints walk, then directive construction (§32/§120).

This builder is the producer of frozen seam C2 (ADR-0023): deterministic ids, the arm's
search region as `brief`, `expected_value` the posterior mean under the cycle's value
model, `budget_limit` the cycle's equal share. ADR-0023 #1's ceiling is enforced here as
a refusal — a portfolio whose shares would cross the cycle's budget raises
`BudgetCeilingExceeded` instead of overspending, and the refusal names the arithmetic.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime

from plm_contracts import AllocationType

from ..config import ARSettings
from ..domain.arm import ContentArm
from ..domain.directive import DirectiveBrief, ProductionDirective
from ..domain.enums import ArmLifecycleStatus
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState
from ..policy.constraints import (
    BudgetCeilingExceeded,
    Portfolio,
    apply_portfolio_constraints,
)
from ..storage.ids import deterministic_id
from .planner import PreparedCycle

__all__ = ["BudgetCeilingExceeded", "build_directives", "build_portfolio"]


def build_portfolio(prepared: PreparedCycle, *, policy: PolicyConfig, slot_count: int,
                    quota: Mapping[AllocationType, int], arms: Mapping[str, ContentArm],
                    states: Mapping[str, ArmLifecycleStatus],
                    baselines: Sequence[str]) -> Portfolio:
    """§58's walk with this cycle's classes, clusters and caps."""
    return apply_portfolio_constraints(
        prepared.ranked, classes=prepared.classes, arms=arms, states=states, policy=policy,
        slot_count=slot_count, quota=quota, baselines=baselines, clusters=prepared.clusters,
    )


def _fraction_for(policy: PolicyConfig, allocation_type: AllocationType) -> float:
    """ADR-0023 #2: the reported fraction is the class's nominal policy slice."""
    return {
        AllocationType.EXPLOIT: policy.exploit_fraction,
        AllocationType.ADJACENT_EXPLORE: policy.adjacent_explore_fraction,
        AllocationType.RADICAL_EXPLORE: policy.radical_explore_fraction,
    }[allocation_type]


def build_directives(portfolio: Portfolio, *, cycle_id: str, policy: PolicyConfig,
                     posteriors: Mapping[str, PosteriorState], arms: Mapping[str, ContentArm],
                     settings: ARSettings, expires_at: datetime, budget_share: float,
                     cycle_budget: float) -> tuple[ProductionDirective, ...]:
    """One directive per selection, portfolio order; priority = 1..n (P3 sorts on it).

    ADR-0023 #1: the cycle owns the ceiling, each directive carries its share. The walk
    cannot select more than `slot_count` arms, so equal shares cannot cross the ceiling;
    this check is the tripwire that makes "never overspend" an invariant rather than an
    argument — a portfolio that would cross it is refused, loudly.
    """
    total = budget_share * len(portfolio.selections)
    if total > cycle_budget + 1e-9:
        raise BudgetCeilingExceeded(
            f"shares would overspend the cycle ceiling: {len(portfolio.selections)} × "
            f"{budget_share} = {total} > {cycle_budget} "
            "(ADR-0023 #1: reduce or refuse, never overspend)"
        )
    directives: list[ProductionDirective] = []
    for position, selection in enumerate(portfolio.selections, start=1):
        arm = arms[selection.arm_id]
        posterior = posteriors[selection.arm_id]
        directives.append(ProductionDirective(
            directive_id=deterministic_id("directive", cycle_id, selection.arm_id,
                                          str(position)),
            decision_id=deterministic_id("decision", cycle_id, selection.arm_id),
            arm_id=selection.arm_id,
            allocation_type=selection.allocation_type,
            priority=position,
            quantity=1,
            brief=DirectiveBrief(
                genre=arm.dimensions.genre, mood=arm.dimensions.moods,
                use_case=arm.dimensions.use_cases, language=arm.dimensions.language,
                vocal_mode=arm.dimensions.vocal_mode,
                duration_bucket=arm.dimensions.duration_bucket,
                visual_world=arm.dimensions.visual_world,
            ),
            expected_value=min(1.0, max(0.0, selection.sample.posterior_expected_reward)),
            uncertainty=math.sqrt(posterior.posterior_variance),
            budget_limit=budget_share,
            market_context={
                "p1_score": arm.p1_score, "p1_confidence": arm.p1_confidence,
                "freshness_seconds": arm.freshness_seconds,
                "score_version": arm.score_version,
                "measured_component_count": arm.measured_component_count,
            },
            selection_reason=selection.reason_codes,
            directive_version=settings.directive_version,
            policy_version=policy.version,
            expires_at=expires_at,
            exploration_fraction=_fraction_for(policy, selection.allocation_type),
            value_model=policy.value_model,
            outcome_horizon=policy.outcome_horizon,
        ))
    return tuple(directives)

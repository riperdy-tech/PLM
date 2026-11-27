# services/autonomous_ar/policy/lifecycle.py
"""§38 retirement and §39 reopening: reversible, evidence-gated, reason-coded.

Retirement needs mature evidence *and* a weak posterior — one bad experiment never
retires an arm (§38). Reopening is the symmetric decision (§39): a materially risen
market score brings a retired arm back, and the reason is stored, never inferred.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ..domain.arm import ContentArm
from ..domain.enums import ArmLifecycleStatus
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState

__all__ = ["REOPEN_SCORE_DELTA", "RetirementDecision", "evaluate_retirements",
           "reopen_reason"]

REOPEN_SCORE_DELTA = 20.0
"""A retired arm returns when P1's score rises this much over the stored snapshot (§39)."""


@dataclass(frozen=True, slots=True)
class RetirementDecision:
    arm_id: str
    reason: str


RETIRABLE_STATES = frozenset({ArmLifecycleStatus.CANDIDATE, ArmLifecycleStatus.ACTIVE})
"""Only these may retire: a BLOCKED (out-of-identity), PAUSED or ARCHIVED arm keeps its
state and reason, so a later reopen can never make it eligible (review 2026-11-26)."""

RETIRED_WEAK = "WEAK_POSTERIOR"


def evaluate_retirements(posteriors: Mapping[str, PosteriorState], *,
                         mature_counts: Mapping[str, int], policy: PolicyConfig,
                         now: datetime,
                         states: Mapping[str, ArmLifecycleStatus] | None = None,
                         ) -> tuple[RetirementDecision, ...]:
    """§38: mature evidence AND a weak posterior — deterministic, sorted by arm id.

    With `states`, only :data:`RETIRABLE_STATES` arms are evaluated."""
    decisions: list[RetirementDecision] = []
    for arm_id, posterior in sorted(posteriors.items()):
        if states is not None and states.get(arm_id) not in RETIRABLE_STATES:
            continue
        mature = mature_counts.get(arm_id, 0)
        if (mature >= policy.retirement.min_mature_observations
                and posterior.posterior_mean < policy.retirement.weak_posterior_threshold):
            decisions.append(RetirementDecision(arm_id=arm_id, reason=RETIRED_WEAK))
    return tuple(decisions)


def reopen_reason(arm: ContentArm, *, stored_score: float, policy: PolicyConfig,
                  state: ArmLifecycleStatus,
                  retirement_reason: str | None = None) -> str | None:
    """§39: a material market rise reopens an arm retired for a weak posterior; the
    reason is stored verbatim. Any other retirement reason (or none) never reopens."""
    if state is not ArmLifecycleStatus.RETIRED or retirement_reason != RETIRED_WEAK:
        return None
    if arm.p1_score - stored_score >= REOPEN_SCORE_DELTA:
        return "market_score_recovered"
    return None

# services/autonomous_ar/policy/constraints.py
"""Portfolio constraints (§15/§25/§58/§59/§61) and the cycle's budget-share arithmetic.

Three passes, one deterministic walk. Pass 1 reserves the baseline share inside the exploit
quota; pass 2 honours each class quota in §58 order; pass 3 is §58's own "continue until slots
are filled" — remaining capacity is filled from the ranked list in the same class order, with
every cap still binding. That is deliberate: a cold start has no measured arms, so every arm is
`radical_explore` and only pass 3 can fill the cycle — which is exactly §60's "increase
exploration rather than pretending certainty" and §69-E's broad exploration. Allocation
fractions therefore *steer* the order; they are never a cage. The budget rule is ADR-0023 #1:
the cycle owns the ceiling, each directive's share is equal, and the walk can never accept more
selections than slots — so `sum(shares) <= ceiling` is structural.

Refusals are auditable exactly once per arm (§58: "otherwise record rejection reason and
continue"): the first pass that turns a candidate away records the reason code and the detail;
later passes retry the candidate without duplicating the record. The exact-arm cap is kept as
§15's guard even though `selected_ids` already enforces the stronger rule that an arm holds at
most one slot, so that cap can never be approached by this walk.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from plm_contracts import AllocationType

from ..domain.arm import ContentArm
from ..domain.decision import ArmSample, ConstraintRejection
from ..domain.enums import ArmLifecycleStatus, ReasonCode
from ..domain.policy import PolicyConfig
from .exploration import reserve_baseline

__all__ = [
    "BudgetCeilingExceeded", "Portfolio", "Selection", "apply_portfolio_constraints",
    "directive_budget_share",
]

_INELIGIBLE_STATES = frozenset({
    ArmLifecycleStatus.BLOCKED, ArmLifecycleStatus.RETIRED, ArmLifecycleStatus.ARCHIVED,
    ArmLifecycleStatus.PAUSED,
})
"""§9/§111: never selected, for any reason, in any mode. `cooldown` is filtered by the
caller with `cooldown_until` (a temporary state, unlike these)."""


class BudgetCeilingExceeded(RuntimeError):
    """Directive shares would sum above the cycle's ceiling (ADR-0023 #1: refuse)."""


@dataclass(frozen=True, slots=True)
class Selection:
    arm_id: str
    allocation_type: AllocationType
    sample: ArmSample
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Portfolio:
    selections: tuple[Selection, ...]
    rejections: tuple[ConstraintRejection, ...]
    warnings: tuple[ReasonCode, ...]

    def is_partial(self, slot_count: int) -> bool:
        """§59: fewer selections than slots is `PARTIAL`, never silently 'completed'."""
        return len(self.selections) < slot_count


def directive_budget_share(cycle_budget: float, slot_count: int) -> float:
    """ADR-0023 #1: the directive carries its share; shares are equal by construction."""
    if slot_count < 1:
        raise ValueError("slot_count must be >= 1")
    if cycle_budget <= 0.0:
        raise ValueError("cycle budget must be > 0")
    return cycle_budget / slot_count


def _share_count(share: float, slot_count: int) -> int:
    """A share cap as a whole number of slots; always at least 1 when the cap is positive."""
    return max(1, math.floor(share * slot_count + 1e-9))


def apply_portfolio_constraints(
    ranked: Sequence[ArmSample], *, classes: Mapping[str, AllocationType],
    arms: Mapping[str, ContentArm], states: Mapping[str, ArmLifecycleStatus],
    policy: PolicyConfig, slot_count: int, quota: Mapping[AllocationType, int],
    baselines: Sequence[str], clusters: Mapping[str, str],
) -> Portfolio:
    """§58's deterministic walk over a ranked list, one class quota at a time."""
    selections: list[Selection] = []
    rejections: list[ConstraintRejection] = []
    warnings: list[ReasonCode] = []
    selected_ids: set[str] = set()
    recorded_refusals: set[str] = set()
    per_arm: dict[str, int] = {}
    per_cluster: dict[str, int] = {}
    per_genre: dict[str, int] = {}
    per_use_case: dict[str, int] = {}
    per_language: dict[str, int] = {}

    max_arm = _share_count(policy.max_exact_arm_share, slot_count)
    max_cluster = _share_count(policy.max_cluster_share, slot_count)
    max_genre = _share_count(policy.max_genre_share, slot_count)
    max_use_case = _share_count(policy.max_use_case_share, slot_count)
    max_language = _share_count(policy.max_language_share, slot_count)

    def _reject(sample: ArmSample, detail: str) -> None:
        """§58: the first refusal is the auditable one; retries never add a second row."""
        if sample.arm_id in recorded_refusals:
            return
        recorded_refusals.add(sample.arm_id)
        rejections.append(ConstraintRejection(
            arm_id=sample.arm_id, reason=ReasonCode.DIVERSITY_CONSTRAINT, detail=detail,
        ))

    def _ineligible_detail(sample: ArmSample) -> str:
        return f"lifecycle state {states.get(sample.arm_id)} is never selected (§111)"

    def _try_accept(sample: ArmSample, allocation_type: AllocationType,
                    extra_reasons: tuple[str, ...]) -> bool:
        arm = arms[sample.arm_id]
        cluster = clusters.get(sample.arm_id, sample.arm_id)
        genre = arm.dimensions.genre or "unknown"
        use_case = (arm.dimensions.use_cases or ("unknown",))[0]
        language = arm.dimensions.language or "unknown"
        checks = [
            (per_arm.get(sample.arm_id, 0) + 1 > max_arm, "exact_arm_share",
             f"exact-arm cap {policy.max_exact_arm_share}"),
            (per_cluster.get(cluster, 0) + 1 > max_cluster, "cluster_share",
             f"cluster cap {policy.max_cluster_share}"),
            (per_cluster.get(cluster, 0) + 1 > policy.max_concurrent_experiments_per_cluster,
             "cluster_concurrency", "§25 concurrent-experiment limit"),
            (per_genre.get(genre, 0) + 1 > max_genre, "genre_share",
             f"genre cap {policy.max_genre_share}"),
            (per_use_case.get(use_case, 0) + 1 > max_use_case, "use_case_share",
             f"use-case cap {policy.max_use_case_share}"),
            (per_language.get(language, 0) + 1 > max_language, "language_share",
             f"language cap {policy.max_language_share}"),
        ]
        for violated, code, detail in checks:
            if violated:
                _reject(sample, f"{detail} ({code})")
                return False
        selections.append(Selection(arm_id=sample.arm_id, allocation_type=allocation_type,
                                    sample=sample, reason_codes=extra_reasons))
        selected_ids.add(sample.arm_id)
        per_arm[sample.arm_id] = per_arm.get(sample.arm_id, 0) + 1
        per_cluster[cluster] = per_cluster.get(cluster, 0) + 1
        per_genre[genre] = per_genre.get(genre, 0) + 1
        per_use_case[use_case] = per_use_case.get(use_case, 0) + 1
        per_language[language] = per_language.get(language, 0) + 1
        return True

    # Pass 1 — baseline reservation (§40/§41), inside the exploit quota.
    reserved = reserve_baseline(slot_count, policy) if baselines else 0
    baseline_set = set(baselines)
    filled_baseline = 0
    for sample in ranked:
        if filled_baseline >= reserved or sample.arm_id not in baseline_set:
            continue
        if states.get(sample.arm_id) in _INELIGIBLE_STATES:
            continue
        if _try_accept(sample, AllocationType.EXPLOIT,
                       (ReasonCode.BASELINE_CONTROL.value, ReasonCode.LOW_FATIGUE.value)):
            filled_baseline += 1

    # Pass 2 — one class quota at a time, §58 order.
    for allocation_type in (AllocationType.EXPLOIT, AllocationType.ADJACENT_EXPLORE,
                            AllocationType.RADICAL_EXPLORE):
        target = quota.get(allocation_type, 0)
        if allocation_type is AllocationType.EXPLOIT:
            target = max(0, target - filled_baseline)
        taken = 0
        for sample in ranked:
            if taken >= target:
                break
            if sample.arm_id in selected_ids or classes.get(sample.arm_id) is not allocation_type:
                continue
            if states.get(sample.arm_id) in _INELIGIBLE_STATES:
                _reject(sample, _ineligible_detail(sample))
                continue
            reasons = (ReasonCode.STRONG_POSTERIOR.value,
                       ReasonCode.MARKET_SIGNAL_STRONG.value) \
                if allocation_type is AllocationType.EXPLOIT else (
                    ReasonCode.ADJACENT_TO_WINNER.value
                    if allocation_type is AllocationType.ADJACENT_EXPLORE
                    else ReasonCode.RADICAL_EXPLORATION.value,
                    ReasonCode.HIGH_UNCERTAINTY.value,
                )
            if _try_accept(sample, allocation_type, reasons):
                taken += 1

    # Pass 3 — §58's fill-until-slots: the quota steers order, the walk fills capacity.
    # A cold start has all arms in `radical_explore` (no measured evidence yet), so this pass
    # is what fills its slots with broad exploration — which is exactly §60's and §69-E's
    # expectation. Caps still bind, and a refusal no earlier pass could record (pass 2 stops
    # at its quota) is recorded here; `_reject` dedupes, so no arm is refused twice on paper.
    for allocation_type in (AllocationType.EXPLOIT, AllocationType.ADJACENT_EXPLORE,
                            AllocationType.RADICAL_EXPLORE):
        if len(selections) >= slot_count:
            break
        for sample in ranked:
            if len(selections) >= slot_count:
                break
            if sample.arm_id in selected_ids or classes.get(sample.arm_id) is not allocation_type:
                continue
            if states.get(sample.arm_id) in _INELIGIBLE_STATES:
                _reject(sample, _ineligible_detail(sample))
                continue
            reasons = ((ReasonCode.STRONG_POSTERIOR.value,
                        ReasonCode.MARKET_SIGNAL_STRONG.value)
                       if allocation_type is AllocationType.EXPLOIT else (
                           ReasonCode.ADJACENT_TO_WINNER.value
                           if allocation_type is AllocationType.ADJACENT_EXPLORE
                           else ReasonCode.RADICAL_EXPLORATION.value,
                           ReasonCode.HIGH_UNCERTAINTY.value,
                       ))
            _try_accept(sample, allocation_type, reasons)

    if len(selections) < slot_count:
        warnings.append(ReasonCode.INSUFFICIENT_ELIGIBLE_ARMS)
    return Portfolio(selections=tuple(selections), rejections=tuple(rejections),
                     warnings=tuple(warnings))

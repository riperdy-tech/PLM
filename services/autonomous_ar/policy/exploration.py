# services/autonomous_ar/policy/exploration.py
"""Exploration arithmetic (§13/§14/§60/§85): quotas, class evidence, seeded sampling.

The three discrete classes are the contract (ADR-0023 #2). `exploration_fraction` is
reporting only; no function here branches on it. Apportionment is largest-remainder so
counts are exact, deterministic and sum to the slot count.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence

from plm_contracts import AllocationType

from ..domain.arm import ContentArm
from ..domain.decision import ArmSample
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState
from ..modeling.reward_model import RewardModel

__all__ = [
    "effective_fractions", "evidence_class", "quota_from_fractions", "reserve_baseline",
    "sample_rewards", "sampled_ranking",
]


def effective_fractions(policy: PolicyConfig, *, low_confidence: bool,
                        regime_shift: bool) -> tuple[float, float, float]:
    """§60/§85: raise exploration from the exploit slice when evidence is thin or shifting."""
    exploit, adjacent, radical = (
        policy.exploit_fraction, policy.adjacent_explore_fraction,
        policy.radical_explore_fraction,
    )
    if low_confidence:
        moved = min(policy.exploration_boost, exploit)
        exploit, radical = exploit - moved, radical + moved
    if regime_shift:
        moved = min(policy.exploration_boost / 2.0, exploit)
        exploit = exploit - moved
        adjacent += moved / 2.0
        radical += moved / 2.0
    return (exploit, adjacent, radical)


SMALL_CYCLE_SLOTS = 3
"""Below this many slots each slot's class is drawn with the policy weights: largest
remainder gives one slot to exploit every time, so the exploration policy did nothing in
the Studio's one-video cycles (review 2026-11-26)."""


def quota_from_fractions(slot_count: int, fractions: tuple[float, float, float], *,
                         rng: random.Random | None = None) -> dict[AllocationType, int]:
    """Largest-remainder apportionment; counts sum exactly to `slot_count`.

    With `rng` and fewer than :data:`SMALL_CYCLE_SLOTS` slots, each slot's class is a
    seeded draw weighted by `fractions` instead (the counts still sum to `slot_count`).
    """
    if slot_count < 1:
        raise ValueError("slot_count must be >= 1")
    classes = (AllocationType.EXPLOIT, AllocationType.ADJACENT_EXPLORE,
               AllocationType.RADICAL_EXPLORE)
    if rng is not None and slot_count < SMALL_CYCLE_SLOTS:
        drawn = dict.fromkeys(classes, 0)
        for _ in range(slot_count):
            point, cumulative = rng.random() * sum(fractions), 0.0
            chosen = classes[0]
            for allocation, fraction in zip(classes, fractions, strict=True):
                cumulative += fraction
                if fraction > 0.0 and point < cumulative:
                    chosen = allocation
                    break
            drawn[chosen] += 1
        return drawn
    raw = [slot_count * fraction for fraction in fractions]
    counts = [math.floor(value) for value in raw]
    remainder = slot_count - sum(counts)
    # hand out the remaining seats to the largest fractional parts; ties by class order
    order = sorted(range(3), key=lambda index: (-(raw[index] - counts[index]), index))
    for index in order[:remainder]:
        counts[index] += 1
    return dict(zip(classes, counts, strict=True))


def reserve_baseline(slot_count: int, policy: PolicyConfig) -> int:
    """§40/§41: the evergreen control share, carved out of the exploit quota."""
    if policy.baseline_fraction <= 0.0:
        return 0
    return min(slot_count, max(1, math.ceil(policy.baseline_fraction * slot_count)))


def evidence_class(arm_id: str, *, measured_arm_ids: frozenset[str],
                   adjacent_arm_ids: frozenset[str]) -> AllocationType:
    """§14: measured arms exploit; unmeasured arms adjacent or radical."""
    if arm_id in measured_arm_ids:
        return AllocationType.EXPLOIT
    if arm_id in adjacent_arm_ids:
        return AllocationType.ADJACENT_EXPLORE
    return AllocationType.RADICAL_EXPLORE


def sample_rewards(arms: Sequence[ContentArm], posteriors: Mapping[str, PosteriorState], *,
                   model: RewardModel, rng: random.Random) -> tuple[ArmSample, ...]:
    """One seeded Thompson draw per arm; a missing posterior is a caller bug (§66)."""
    samples: list[ArmSample] = []
    for arm in arms:
        posterior = posteriors[arm.arm_id]  # KeyError is the loud failure
        samples.append(ArmSample(
            arm_id=arm.arm_id, sampled_reward=model.sample(posterior, rng),
            posterior_expected_reward=model.expected_value(posterior),
        ))
    return tuple(samples)


def sampled_ranking(samples: Sequence[ArmSample], *,
                    tiebreak: Mapping[str, float] | None = None) -> tuple[ArmSample, ...]:
    """§58: sampled reward descending, then the seeded `tiebreak` value, then `arm_id`
    ascending — a deterministic total order.

    Clamped draws tie often (every draw above 1.0 is 1.0); breaking those ties on
    `arm_id` alone always gave the same arm the slot, so the cycle passes a seeded draw
    per arm (review 2026-11-26). Without one, ties break on `arm_id` as before.
    """
    breaks = tiebreak or {}
    return tuple(sorted(samples, key=lambda sample: (
        -sample.sampled_reward, breaks.get(sample.arm_id, 0.0), sample.arm_id)))

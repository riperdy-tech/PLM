import math
from datetime import UTC, datetime

import pytest
from plm_contracts import AllocationType

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.decision import ArmSample
from services.autonomous_ar.domain.enums import ArmLifecycleStatus, ReasonCode
from services.autonomous_ar.domain.policy import DEFAULT_POLICY, PolicyConfig
from services.autonomous_ar.policy.constraints import (
    apply_portfolio_constraints,
    directive_budget_share,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _arm(arm_id: str, *, genre: str = "rnb", use_case: str = "late_night_drive",
         language: str = "english") -> ContentArm:
    return ContentArm(arm_id=arm_id,
                      dimensions=ArmDimensions(genre=genre, use_cases=(use_case,),
                                               language=language),
                      p1_score=60.0, p1_confidence=0.6, component_scores=COMPONENTS,
                      observation_count=1, source_count=1, as_of=NOW, score_version="p1-v1",
                      freshness_seconds=0.0)


def _ranked(count: int, *, genre: str | None = None, use_case: str | None = None,
            language: str | None = None) -> tuple[list[ArmSample], dict[str, ContentArm]]:
    """`count` candidates; only the dimensions named explicitly are shared by every arm.

    The plan's original helper gave every arm `genre="rnb"`, which the genre/use-case caps
    then cut to 6 selections when a test expected 10 — the data contradicted the caps.
    """
    samples: list[ArmSample] = []
    arms: dict[str, ContentArm] = {}
    for index in range(count):
        arm_id = f"arm-{index:02d}"
        samples.append(ArmSample(arm_id=arm_id, sampled_reward=0.9 - index * 0.01,
                                 posterior_expected_reward=0.7))
        arms[arm_id] = _arm(arm_id,
                            genre=genre if genre is not None else f"genre-{index}",
                            use_case=use_case if use_case is not None else f"use-{index}",
                            language=language if language is not None else f"lang-{index}")
    return samples, arms


def test_walk_honours_the_exact_arm_cap() -> None:
    samples, arms = _ranked(10)
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=10,
        quota={AllocationType.EXPLOIT: 10, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0},
        baselines=(), clusters={arm_id: f"c-{index}" for index, arm_id in enumerate(arms)},
    )
    assert len(portfolio.selections) == 10
    assert len({selection.arm_id for selection in portfolio.selections}) == 10


def test_blocked_and_retired_arms_are_never_selected() -> None:
    samples, arms = _ranked(2)
    states = {"arm-00": ArmLifecycleStatus.BLOCKED, "arm-01": ArmLifecycleStatus.RETIRED}
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states=states, policy=DEFAULT_POLICY, slot_count=2,
        quota={AllocationType.EXPLOIT: 2, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0}, baselines=(),
        clusters={arm_id: arm_id for arm_id in arms},
    )
    assert portfolio.selections == ()
    assert all(rejection.reason is ReasonCode.DIVERSITY_CONSTRAINT
               for rejection in portfolio.rejections)
    assert ReasonCode.INSUFFICIENT_ELIGIBLE_ARMS in portfolio.warnings


def test_genre_cap_and_cluster_concurrency_hold() -> None:
    samples, arms = _ranked(10, genre="rnb")
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=10,
        quota={AllocationType.EXPLOIT: 10, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0}, baselines=(),
        clusters={arm_id: "one-cluster" for arm_id in arms},
    )
    # genre cap 0.60 × 10 slots ⇒ at most 6; but cluster concurrency 2 binds first
    assert len(portfolio.selections) == 2
    assert any("cluster_concurrency" in rejection.detail for rejection in portfolio.rejections)


def test_baseline_reservation_is_honoured_and_budget_shares_stay_under_the_ceiling() -> None:
    samples, arms = _ranked(10)
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=10,
        quota={AllocationType.EXPLOIT: 10, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0}, baselines=("arm-08",),
        clusters={arm_id: arm_id for arm_id in arms},
    )
    baseline_selection = next(s for s in portfolio.selections if s.arm_id == "arm-08")
    assert ReasonCode.BASELINE_CONTROL.value in baseline_selection.reason_codes
    share = directive_budget_share(cycle_budget=10.0, slot_count=10)
    assert share == 1.0
    assert len(portfolio.selections) * share <= 10.0 + 1e-9
    # the reservation is inside the exploit quota: 1 baseline + 9 others = 10, never 11
    assert len(portfolio.selections) == 10


def test_the_walk_is_deterministic_for_the_same_inputs() -> None:
    samples, arms = _ranked(6)
    args: dict[str, object] = {
        "classes": {arm_id: AllocationType.EXPLOIT for arm_id in arms}, "arms": arms,
        "states": {arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        "policy": DEFAULT_POLICY, "slot_count": 6,
        "quota": {AllocationType.EXPLOIT: 6, AllocationType.ADJACENT_EXPLORE: 0,
                  AllocationType.RADICAL_EXPLORE: 0}, "baselines": (),
        "clusters": {arm_id: arm_id for arm_id in arms},
    }
    first = apply_portfolio_constraints(samples, **args)
    second = apply_portfolio_constraints(list(samples), **args)
    assert first == second
    assert math.floor(DEFAULT_POLICY.max_exact_arm_share * 6) <= 3


_CAP_CASES = [
    # (code, policy, dimensions shared by every arm, one shared cluster?, expected slots)
    ("cluster_share", PolicyConfig(max_concurrent_experiments_per_cluster=10),
     {"language": "english"}, True, 6),
    ("genre_share", DEFAULT_POLICY, {"genre": "rnb", "language": "english"}, False, 6),
    ("use_case_share", DEFAULT_POLICY,
     {"use_case": "late_night_drive", "language": "english"}, False, 6),
    ("language_share", DEFAULT_POLICY, {"language": "english"}, False, 8),
    ("cluster_concurrency", DEFAULT_POLICY, {"genre": "rnb", "language": "english"},
     True, 2),
]


@pytest.mark.parametrize(("code", "policy", "shared", "same_cluster", "expected"),
                         _CAP_CASES)
def test_every_cap_binds_and_records_its_refusal(
        code: str, policy: PolicyConfig, shared: dict[str, str], same_cluster: bool,
        expected: int) -> None:
    """The cap that must bind is the first one to fire, slots stop exactly at it, and every
    refused candidate carries an auditable DIVERSITY_CONSTRAINT row naming the cap."""
    samples, arms = _ranked(10, **shared)
    clusters = {f"arm-{index:02d}": ("one-cluster" if same_cluster else f"c-{index}")
                for index in range(10)}
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=policy, slot_count=10,
        quota={AllocationType.EXPLOIT: 10, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0},
        baselines=(), clusters=clusters,
    )
    assert len(portfolio.selections) == expected
    assert all(rejection.reason is ReasonCode.DIVERSITY_CONSTRAINT
               for rejection in portfolio.rejections)
    assert any(code in rejection.detail for rejection in portfolio.rejections)
    # exactly one refusal record per refused arm: pass 3's retries never duplicate the record
    assert len(portfolio.rejections) == 10 - expected


def test_cold_start_fills_every_slot_through_the_third_pass() -> None:
    """All arms unmeasured (§10) ⇒ every arm is `radical_explore`; the class quota steers
    pass 2 and pass 3 fills the rest (§12b ruling) — §60/§69-E expect broad exploration,
    not an empty cycle. No cap is exceeded while doing it."""
    samples: list[ArmSample] = []
    arms: dict[str, ContentArm] = {}
    clusters: dict[str, str] = {}
    for index in range(10):
        arm_id = f"arm-{index:02d}"
        samples.append(ArmSample(arm_id=arm_id, sampled_reward=0.9 - index * 0.01,
                                 posterior_expected_reward=0.7))
        arms[arm_id] = _arm(arm_id, genre="rnb" if index < 5 else "jazz",
                            use_case="late_night_drive" if index < 5 else "focus",
                            language="english" if index < 5 else "korean")
        clusters[arm_id] = f"c-{index}"
    quota = {AllocationType.EXPLOIT: 0, AllocationType.ADJACENT_EXPLORE: 0,
             AllocationType.RADICAL_EXPLORE: 2}
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.RADICAL_EXPLORE for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=10, quota=quota, baselines=(), clusters=clusters,
    )
    assert sum(quota.values()) < len(portfolio.selections)  # pass 3 did the filling
    assert len(portfolio.selections) == 10
    assert [selection.arm_id for selection in portfolio.selections] == \
        [f"arm-{index:02d}" for index in range(10)]  # ranked order, deterministic
    assert {selection.allocation_type for selection in portfolio.selections} == \
        {AllocationType.RADICAL_EXPLORE}
    assert all(ReasonCode.RADICAL_EXPLORATION.value in selection.reason_codes
               for selection in portfolio.selections)
    assert portfolio.rejections == ()
    assert portfolio.warnings == ()
    assert portfolio.is_partial(10) is False


def test_cold_start_caps_stay_binding_and_refusals_are_recorded() -> None:
    """The same cold start with one shared genre: pass 3 fills up to the genre cap and
    stops. §59 wants PARTIAL + a warning rather than a fabricated fill, and §58 wants every
    refusal on the record — including ones pass 2 never scanned (quota reached early)."""
    samples, arms = _ranked(10, genre="rnb")  # all rnb; other dimensions differ per arm
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.RADICAL_EXPLORE for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=10,
        quota={AllocationType.EXPLOIT: 0, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 2},
        baselines=(), clusters={f"arm-{index:02d}": f"c-{index}" for index in range(10)},
    )
    assert len(portfolio.selections) == 6  # genre cap 0.60 × 10 slots, not one slot more
    assert portfolio.is_partial(10) is True
    assert portfolio.warnings == (ReasonCode.INSUFFICIENT_ELIGIBLE_ARMS,)
    assert [rejection.reason for rejection in portfolio.rejections] == \
        [ReasonCode.DIVERSITY_CONSTRAINT] * 4
    assert all("genre_share" in rejection.detail for rejection in portfolio.rejections)


def test_a_repeated_candidate_cannot_take_a_second_slot() -> None:
    """`max_exact_arm_share` is a guard, not a quota: this walk already enforces the
    stronger rule that an arm holds at most one slot, so a duplicate sample of an
    already-selected arm is skipped rather than re-selected (§15/§16 protection)."""
    samples, arms = _ranked(2)
    repeated = [samples[0], samples[0], samples[0], samples[1]]
    portfolio = apply_portfolio_constraints(
        repeated, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=2,
        quota={AllocationType.EXPLOIT: 2, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0},
        baselines=(), clusters={arm_id: arm_id for arm_id in arms},
    )
    assert [selection.arm_id for selection in portfolio.selections] == ["arm-00", "arm-01"]
    assert len({selection.arm_id for selection in portfolio.selections}) == \
        len(portfolio.selections)

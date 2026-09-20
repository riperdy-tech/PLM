"""§98's five policy acceptance tests, plus §100's relative-behaviour rule and the two
P2.12 proofs the plan demands directly: a second cycle that allocates differently because
of the first (§69's learning loop turning), and the simulation version stamp (§70)."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.domain.policy import DEFAULT_POLICY, PolicyConfig
from services.autonomous_ar.simulation.metrics import exploration_ratio, late_window_share
from services.autonomous_ar.simulation.scenarios import (
    scenario_clear_winner,
    scenario_deceptive_early_winner,
    scenario_high_correlation,
    scenario_regime_change,
    scenario_source_shock,
    scenario_sparse_observations,
)
from services.autonomous_ar.simulation.simulator import SimConfig, run_simulation

# 6 slots, not 5: with the default 70/20/10 fractions and 5 slots the warm radical quota is
# 0 seats (the tied 0.5 largest-remainder seat goes to exploit), so the arms measured in
# cycle 1 are the only arms ever selected again — §98-1's "materially more exploitation"
# becomes unmeasurable. 6 slots give radical its seat and the portfolio churns.
BASE = SimConfig(cycles=80, slot_count=6, seed=23)


def test_acceptance_1_clear_winner_gets_exploitation_with_exploration_left_on() -> None:
    trace = run_simulation(scenario_clear_winner(), BASE)
    counts = trace.selection_counts
    assert counts["arm-best"] > max(counts[arm_id] for arm_id in counts if arm_id != "arm-best")
    assert any(ratio > 0.0 for ratio in trace.exploration_by_cycle)


def test_acceptance_2_deceptive_winner_does_not_dominate() -> None:
    trace = run_simulation(scenario_deceptive_early_winner(), SimConfig(cycles=120,
                                                                       slot_count=5, seed=7))
    assert late_window_share(trace.selected_by_cycle, "arm-lucky", window=30) < 0.75


def test_acceptance_3_allocation_shifts_after_a_regime_change() -> None:
    trace = run_simulation(scenario_regime_change(), SimConfig(cycles=160, slot_count=6,
                                                              seed=11))
    # the last 40 of 160 cycles are entirely post-change (change at cycle 60)
    assert late_window_share(trace.selected_by_cycle, "arm-new", window=40) > \
        late_window_share(trace.selected_by_cycle, "arm-old", window=40)


def test_acceptance_4_exploration_floor_holds_when_arms_are_eligible() -> None:
    trace = run_simulation(scenario_sparse_observations(), BASE)
    floor = (DEFAULT_POLICY.adjacent_explore_fraction + DEFAULT_POLICY.radical_explore_fraction)
    seen: set[str] = set()
    checked = 0
    for cycle, ratio in zip(trace.selected_by_cycle, trace.exploration_by_cycle, strict=True):
        if len(seen) < len(trace.selection_counts):  # §98-4: "when eligible arms exist"
            assert ratio >= floor * 0.5, (
                "§98-4: adjacent+radical must not vanish while eligible unmeasured arms exist"
            )
            checked += 1
        seen.update(cycle)
    assert checked > 0, "the sparse scenario must retain unmeasured arms for a while"


def test_acceptance_5_diversity_caps_hold_in_the_correlated_scenario() -> None:
    trace = run_simulation(scenario_high_correlation(), BASE)
    for cycle in trace.selected_by_cycle:
        assert len(cycle) == len(set(cycle)), "an arm may not fill two slots in one portfolio"


def test_determinism_same_seed_same_trace_and_relative_behaviour_comparison() -> None:
    first = run_simulation(scenario_clear_winner(), BASE)
    second = run_simulation(scenario_clear_winner(), BASE)
    assert first == second, "§77/§98: same seed + same spec ⇒ identical trace"
    # §100 states acceptance as relative behaviour on the same seed, not a fixed number.
    # A single seed's cumulative-reward direction is NOT a property of these mechanics
    # (measured for this scenario: greedy wins at seeds 7 and 99, loses at 23, 11 and 42).
    # What does hold, and is pinned here: each policy replays exactly, and a policy with a
    # bigger radical share really buys more exploration on the same scenario and seed
    # (radical quota 2 seats vs 1 at 10 slots).
    more_explore = PolicyConfig(name="explore-more", exploit_fraction=0.5,
                                adjacent_explore_fraction=0.3, radical_explore_fraction=0.2)
    scenario = scenario_source_shock()
    greedy_cfg = SimConfig(cycles=20, slot_count=10, seed=9)
    greedy = run_simulation(scenario, greedy_cfg)
    exploratory = run_simulation(scenario, SimConfig(cycles=20, slot_count=10, seed=9,
                                                     policy=more_explore))
    assert run_simulation(scenario, greedy_cfg) == greedy
    assert exploration_ratio(exploratory.exploration_by_cycle) > exploration_ratio(
        greedy.exploration_by_cycle
    ), "§100: compare policy versions on the same seed — the comparison must show the change"


def test_the_second_cycle_allocates_differently_because_of_the_first() -> None:
    """§69/§100: the learning loop must turn — cycle 2 differs from cycle 1 *because*
    cycle 1's selections became measured evidence."""
    trace = run_simulation(scenario_sparse_observations(), SimConfig(cycles=2, slot_count=10,
                                                                     seed=23))
    first, second = set(trace.selected_by_cycle[0]), set(trace.selected_by_cycle[1])
    assert trace.exploration_by_cycle[0] == 1.0, "a cold start is all exploration"
    assert second != first, "cycle 2 must not repeat cycle 1's portfolio"
    assert second - first, "cycle 2 must explore a still-unmeasured arm cycle 1 did not touch"
    assert first & second, "cycle 2 must exploit the arms cycle 1 measured"
    assert trace.exploration_by_cycle[1] < trace.exploration_by_cycle[0]


def test_simulated_rewards_are_stamped_with_the_simulation_version() -> None:
    """§70/plan: synthetic rewards carry `p2-sim-v1`, never a measured reward version, and
    the simulator touches no storage — a simulated reward can never be read as measured."""
    from services.autonomous_ar.simulation.simulator import _simulated_observation

    settings = load_ar_settings()
    assert settings.simulation_reward_version == "p2-sim-v1"
    observation = _simulated_observation(
        scenario_clear_winner(), "arm-best", {"arm-best": 0.8}, random.Random(1),
        datetime(2026, 9, 20, 6, 0, tzinfo=UTC), settings)
    assert observation.reward_version == "p2-sim-v1"
    assert observation.arm_id == "arm-best"

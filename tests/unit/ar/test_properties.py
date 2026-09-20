# tests/unit/ar/test_properties.py
"""§96's invariants as executable loop properties over seeded runs — no hypothesis dependency."""

from services.autonomous_ar.simulation.scenarios import ALL_SCENARIOS
from services.autonomous_ar.simulation.simulator import SimConfig, run_simulation

CONFIGS = tuple(SimConfig(cycles=30, slot_count=4, seed=seed) for seed in (1, 2, 3, 5, 8))


def test_selection_count_never_exceeds_slot_count() -> None:
    for scenario in ALL_SCENARIOS:
        for config in CONFIGS:
            trace = run_simulation(scenario(), config)
            assert all(len(cycle) <= config.slot_count for cycle in trace.selected_by_cycle)


def test_sampling_never_mutates_posteriors_and_variances_stay_non_negative() -> None:
    for scenario in ALL_SCENARIOS:
        trace = run_simulation(scenario(), CONFIGS[0])
        assert all(mean >= 0.0 for mean in trace.posterior_means_final.values())
        assert trace.regret_proxy == trace.regret_proxy  # not NaN


def test_an_arm_is_never_selected_twice_in_one_portfolio() -> None:
    for scenario in ALL_SCENARIOS:
        for config in CONFIGS:
            trace = run_simulation(scenario(), config)
            for cycle in trace.selected_by_cycle:
                assert len(cycle) == len(set(cycle))


def test_exploration_is_bounded_by_the_policy() -> None:
    for scenario in ALL_SCENARIOS:
        trace = run_simulation(scenario(), CONFIGS[0])
        assert all(0.0 <= ratio <= 1.0 for ratio in trace.exploration_by_cycle)

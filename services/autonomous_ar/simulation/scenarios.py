# services/autonomous_ar/simulation/scenarios.py
"""§69's seven synthetic environments. True means exist only here (§70)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = [
    "ALL_SCENARIOS", "ScenarioSpec", "SimArm", "scenario_clear_winner",
    "scenario_deceptive_early_winner", "scenario_high_correlation",
    "scenario_regime_change", "scenario_source_shock", "scenario_sparse_observations",
    "scenario_two_close_winners",
]


@dataclass(frozen=True, slots=True)
class SimArm:
    arm_id: str
    genre: str
    use_case: str
    language: str
    true_mean: float
    true_variance: float


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    name: str
    arms: tuple[SimArm, ...]
    p1_scores: Mapping[str, float]
    regime_change_at: int | None = None
    regime_means: Mapping[str, float] = field(default_factory=dict)
    shock_at: int | None = None
    shocked_scores: Mapping[str, float] = field(default_factory=dict)


def _arm(arm_id: str, mean: float, *, genre: str = "rnb",
         use_case: str | None = None, language: str | None = None,
         variance: float = 0.01) -> SimArm:
    """One synthetic arm. `use_case`/`language` default to arm-unique values so the §15
    portfolio caps do not bind in the *mechanical* scenarios (the plan's uniform default
    collapsed every scenario to the same <=3 arms forever, freezing §69's behaviours);
    `genre` stays shared by design so scenario F still exercises the genre cap."""
    return SimArm(arm_id=arm_id, genre=genre,
                  use_case=use_case or f"uc-{arm_id}",
                  language=language or f"lang-{arm_id}",
                  true_mean=mean, true_variance=variance)


def scenario_clear_winner() -> ScenarioSpec:
    """One material winner among eleven middling arms (§69-A).

    Twelve arms, not six: with arms <= slots every measured arm is selected every cycle,
    counts tie and §98-1's "materially more exploitation" is unmeasurable in the trace.
    """
    arms = tuple([_arm("arm-best", 0.80)]
                 + [_arm(f"arm-mid-{index}", 0.45 - index * 0.02, genre=f"gc{index}")
                    for index in range(11)])
    return ScenarioSpec("clear_winner", arms,
                        {arm.arm_id: 80.0 - 4.0 * index for index, arm in enumerate(arms)})


def scenario_two_close_winners() -> ScenarioSpec:
    arms = (_arm("arm-a", 0.70), _arm("arm-b", 0.68, genre="city_pop"),
            _arm("arm-c", 0.35, genre="house"), _arm("arm-d", 0.30, genre="jazz"),
            _arm("arm-e", 0.25, genre="ambient"))
    return ScenarioSpec("two_close_winners", arms,
                        {arm.arm_id: 75.0 - index * 8.0 for index, arm in enumerate(arms)})


def scenario_deceptive_early_winner() -> ScenarioSpec:
    arms = (_arm("arm-lucky", 0.45, variance=0.16), _arm("arm-steady", 0.62),
            _arm("arm-low", 0.30, genre="house"))
    return ScenarioSpec("deceptive_early_winner", arms,
                        {"arm-lucky": 88.0, "arm-steady": 60.0, "arm-low": 45.0})


def scenario_regime_change() -> ScenarioSpec:
    """The previous winner weakens and the challenger strengthens at cycle 60 (§69-D).

    The six mid arms (0.55) keep slots contested: with only two arms every arm is selected
    every cycle, no answer can change the portfolio, and the challenger — low on evidence —
    would starve forever (an unselected arm never learns), so adaptation would be
    unobservable in the trace regardless of the regime flip.
    """
    arms = (_arm("arm-old", 0.75), _arm("arm-new", 0.30, genre="city_pop"),
            *tuple(_arm(f"arm-mid-{index}", 0.55, genre=f"gm{index}") for index in range(6)))
    return ScenarioSpec("regime_change", arms,
                        {arm.arm_id: 85.0 - index * 5.0 for index, arm in enumerate(arms)},
                        regime_change_at=60, regime_means={"arm-old": 0.25, "arm-new": 0.75})


def scenario_sparse_observations() -> ScenarioSpec:
    arms = tuple(_arm(f"arm-{index:02d}", 0.45, genre=f"g{index % 3}")
                 for index in range(12))
    return ScenarioSpec("sparse_observations", arms,
                        {arm.arm_id: 55.0 for arm in arms})


def scenario_high_correlation() -> ScenarioSpec:
    arms = tuple(_arm(f"arm-rnb-{index}", 0.55, variance=0.02) for index in range(5)) + (
        _arm("arm-far-1", 0.45, genre="city_pop"), _arm("arm-far-2", 0.40, genre="jazz"),
    )
    return ScenarioSpec("high_correlation", arms, {arm.arm_id: 70.0 for arm in arms})


def scenario_source_shock() -> ScenarioSpec:
    """Sixty arms so unmeasured (radically explorable) arms still exist at `shock_at`:
    the market signal degrades at cycle 20 and exploration must not vanish with it (§69-G).
    """
    arms = tuple(_arm(f"arm-{index:02d}", 0.45, genre=f"g{index}") for index in range(60))
    return ScenarioSpec("source_shock", arms, {arm.arm_id: 70.0 for arm in arms},
                        shock_at=20, shocked_scores={arm.arm_id: 5.0 for arm in arms})


ALL_SCENARIOS = (scenario_clear_winner, scenario_two_close_winners,
                 scenario_deceptive_early_winner, scenario_regime_change,
                 scenario_sparse_observations, scenario_high_correlation,
                 scenario_source_shock)

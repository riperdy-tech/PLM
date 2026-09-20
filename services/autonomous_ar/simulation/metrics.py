# services/autonomous_ar/simulation/metrics.py
"""§99's benchmark metrics, computed from a trace. Numbers to compare policies, not promises."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["coverage", "cumulative_reward", "exploration_ratio", "late_window_share",
           "winner_concentration"]


def cumulative_reward(rewards: Sequence[float]) -> float:
    return float(sum(rewards))


def winner_concentration(selection_counts: Mapping[str, int]) -> float:
    """Normalised Herfindahl index in [1/n, 1]; 1.0 means everything went to one arm."""
    total = sum(selection_counts.values())
    if total == 0:
        return 0.0
    shares = [count / total for count in selection_counts.values()]
    return float(sum(share * share for share in shares))


def exploration_ratio(exploration_by_cycle: Sequence[float]) -> float:
    """Mean share of each cycle's selections that were exploration classes."""
    if not exploration_by_cycle:
        return 0.0
    return float(sum(exploration_by_cycle) / len(exploration_by_cycle))


def coverage(selection_counts: Mapping[str, int]) -> float:
    """Fraction of arms that were selected at least once."""
    if not selection_counts:
        return 0.0
    return sum(1 for count in selection_counts.values() if count > 0) / len(selection_counts)


def late_window_share(selected_by_cycle: Sequence[Sequence[str]], arm_id: str, *,
                      window: int) -> float:
    """Share of a named arm among the last `window` cycles' selections."""
    if not selected_by_cycle:
        return 0.0
    tail = selected_by_cycle[-window:]
    total = sum(len(cycle) for cycle in tail)
    if total == 0:
        return 0.0
    return sum(1 for cycle in tail for selected in cycle if selected == arm_id) / total

# services/market_intelligence/scoring/normalization.py
"""§18: winsorize, then robust percentile anchors — never min/max on a tiny sample."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

__all__ = ["DEFAULT_ANCHORS", "percentile_score", "winsorize"]

DEFAULT_ANCHORS: Final[tuple[float, float, float]] = (0.10, 0.50, 0.90)


def _percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile — the form the scoring anchors need (p50 of
    1..100 lands on 50.5). Deterministic: `sorted` only, no dependence on input order.
    """
    if not values:
        raise ValueError("percentile of an empty sample is undefined")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = min(max(q, 0.0), 1.0) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def _bound_value(values: Sequence[float], q: float) -> float:
    """The **observed** value at the q-th percentile position — no interpolation.

    Winsorization must clamp to real observations: the plan's own winsorize test pins
    the sample minimum staying 1.0 while the 1000.0 outlier is pulled to the 90th
    percentile *value* (9.0), which interpolated bounds (p10 = 1.9) cannot satisfy.
    Interpolation belongs to `percentile_score`'s anchors, not to the clamp bounds.
    """
    ordered = sorted(values)
    index = min(max(int(min(max(q, 0.0), 1.0) * len(ordered)) - 1, 0), len(ordered) - 1)
    return float(ordered[index])


def winsorize(
    values: Sequence[float], *, lower_pct: float = 0.10, upper_pct: float = 0.90,
) -> list[float]:
    """Clamp the tails to their percentiles so one outlier cannot dominate (§18)."""
    if not values:
        return []
    low = _bound_value(values, lower_pct)
    high = _bound_value(values, upper_pct)
    if high < low:
        raise ValueError("upper percentile below lower percentile")
    return [min(max(float(value), low), high) for value in values]


def percentile_score(
    value: float, *, population: Sequence[float], anchors: Sequence[float] = DEFAULT_ANCHORS,
) -> float:
    """§18's 0–100 rank score: p10→0, p50→50, p90→100, linear between the anchors.

    Outside the anchors the score is clamped, which keeps an extreme outlier in the
    population from stretching the scale for everyone else. A degenerate population
    (one distinct value) has no spread to rank against: 50.0 — the plan's own test
    asserts the single-value case, which the plan's code returned as 0.0.
    """
    if not population:
        return 50.0
    low_q, mid_q, high_q = anchors
    low = _percentile(population, low_q)
    mid = _percentile(population, mid_q)
    high = _percentile(population, high_q)
    if high <= low:
        return 50.0
    if value <= low:
        return 0.0
    if value >= high:
        return 100.0
    if value <= mid:
        span = mid - low
        return 50.0 if span <= 0 else 50.0 * (value - low) / span
    span = high - mid
    return 100.0 if span <= 0 else 50.0 + 50.0 * (value - mid) / span

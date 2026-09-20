# services/market_intelligence/features/growth.py
"""§12: growth at the arm level, with explicit non-overlapping windows.

A baseline too small to support a percentage returns `None`/unknown — §12's
"mark growth as unstable rather than producing absurd percentages".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation
from .velocity import lifetime_views_per_day, windowed_views_per_day

__all__ = [
    "ARM_WINDOW_DAYS",
    "growth_30d_vs_prev30d",
    "growth_30d_vs_prev90d",
    "growth_rate",
    "median_velocity",
    "window_bounds",
]

ARM_WINDOW_DAYS = 30
_SEQUENCE_GAP_DAYS = 1.0  # two sightings closer than this cannot form a window


def window_bounds(
    as_of: datetime, *, days: int, offset_windows: int = 0,
) -> tuple[datetime, datetime]:
    """Half-open `[start, end)` bounds for the window ending `offset_windows` back."""
    end = as_of - timedelta(days=days * offset_windows)
    return end - timedelta(days=days), end


def growth_rate(current: float, baseline: float, *, minimum_baseline: float = 1.0) -> float | None:
    """§12's formula; `None` when the baseline cannot support a percentage."""
    if abs(baseline) < minimum_baseline:
        return None
    return (current - baseline) / max(abs(baseline), 1e-9)


def median_velocity(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, window_days: int,
) -> Tri:
    """Median of in-window view deltas (§11.5/§12's suggested features), windowed only.

    The metric is computed from in-window sightings alone. It used to average the
    lifetime fallback of the newest sighting into the same median, which let an old
    channel's cumulative average outvote its own slow deltas and flipped §35's
    ranking as soon as one sighting moved out of the window (review fix 5). When the
    window holds too few sightings for even one delta, the metric is *insufficient* —
    `Tri.unknown()`, never a fallback value.
    """
    start, end = window_bounds(as_of, days=window_days)
    inside = [o for o in observations if start <= o.observed_at < end]
    if not inside:
        return Tri.unknown()
    velocities: list[float] = []
    by_entity: dict[str, list[NormalizedObservation]] = {}
    for observation in inside:
        by_entity.setdefault(observation.entity_id, []).append(observation)
    for series in by_entity.values():
        ordered = sorted(series, key=lambda o: o.observed_at)
        for earlier, later in pairwise(ordered):
            delta_days = (later.observed_at - earlier.observed_at).total_seconds() / 86400.0
            if delta_days >= _SEQUENCE_GAP_DAYS:
                velocity = windowed_views_per_day(
                    earlier.views, later.views, observed_at_before=earlier.observed_at,
                    observed_at_after=later.observed_at,
                )
            else:
                velocity = Tri.unknown()
            if velocity.is_known and velocity.unwrap() >= 0:
                velocities.append(float(velocity.unwrap()))
    if not velocities:
        return Tri.unknown()
    ordered_values = sorted(velocities)
    middle = len(ordered_values) // 2
    if len(ordered_values) % 2:
        return Tri.known(ordered_values[middle])
    return Tri.known((ordered_values[middle - 1] + ordered_values[middle]) / 2.0)


def growth_30d_vs_prev30d(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, settings=None,
) -> Tri:
    """Recent 30 days against the preceding comparable 30 days (§12)."""
    # The plan passed offset=0, which made the baseline window identical to the
    # current one (growth ≡ 0). Window 1 is the preceding window — the plan's own
    # window test pins `previous_end == current_start`.
    return _growth_between(observations, as_of=as_of, days=ARM_WINDOW_DAYS, offset=1)


def growth_30d_vs_prev90d(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, settings=None,
) -> Tri:
    """Recent 30 days against the preceding 90-day window (the robust second metric)."""
    return _growth_between(observations, as_of=as_of, days=ARM_WINDOW_DAYS * 3, offset=1)


def _growth_between(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, days: int, offset: int,
) -> Tri:
    current = _window_median(observations, as_of=as_of, days=ARM_WINDOW_DAYS, offset=0)
    baseline = _window_median(observations, as_of=as_of, days=days, offset=offset)
    if not (current.is_known and baseline.is_known):
        return Tri.unknown()
    rate = growth_rate(float(current.unwrap()), float(baseline.unwrap()))
    return Tri.unknown() if rate is None else Tri.known(rate)


def _window_median(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, days: int, offset: int,
) -> Tri:
    start, end = window_bounds(as_of, days=days, offset_windows=offset)
    inside = [o for o in observations if start <= o.observed_at < end]
    values: list[float] = []
    for observation in inside:
        if observation.published_at is not None and observation.views.is_known:
            velocity = lifetime_views_per_day(
                observation.views, observed_at=observation.observed_at,
                published_at=observation.published_at,
            )
            values.append(float(velocity.unwrap()))
    if len(values) < 2:  # §19: one data point is not a distribution
        return Tri.unknown()
    values.sort()
    middle = len(values) // 2
    return Tri.known(
        values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    )

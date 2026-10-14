# services/market_intelligence/features/growth.py
"""§12: growth at the arm level, over explicit, named windows.

Every window is a half-open span of days *before* `as_of`, and its name says the
span: `d0_30` is `[as_of - 30d, as_of)`, `d90_180` is `[as_of - 180d, as_of - 90d)`.
The windows used here:

* `d0_30`   — the current window (§12 "recent 30 days");
* `d30_60`  — the preceding comparable 30 days (§12's baseline, `growth_30d_vs_prev30d`);
* `d30_90`  — the rest of the trailing quarter before the current window;
* `d90_180` — the prior quarter, the historical baseline (§17.3
  `growth_30d_vs_historical_baseline`).

The spec's `growth_30d_vs_prev90d` was implemented as "90 days, one window back",
i.e. days 90–180, under a name that read as "the previous 90 days"; it is replaced by
the two explicitly named spans above (review R2).

Each window's activity is the median of **windowed view deltas** (§11.5) between
sightings of the same entity that both fall inside that window — never the lifetime
views/day of a single sighting, which grows with a video's age. A window with no
measurable delta is unknown, not zero, and growth against it is unknown. A baseline
too small to support a percentage is also unknown (§12: "mark growth as unstable
rather than producing absurd percentages").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from statistics import median
from typing import Final

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation
from .velocity import recent_views_per_day, windowed_views_per_day

__all__ = [
    "ARM_WINDOW_DAYS",
    "CURRENT_D0_30",
    "HISTORICAL_D90_180",
    "PRIOR_D30_60",
    "PRIOR_D30_90",
    "DayWindow",
    "growth_between",
    "growth_d0_30_vs_d30_60",
    "growth_d0_30_vs_d30_90",
    "growth_d0_30_vs_d90_180",
    "growth_rate",
    "median_recent_velocity",
    "median_velocity",
    "window_bounds",
    "window_median_velocity",
]

ARM_WINDOW_DAYS = 30
_SEQUENCE_GAP_DAYS = 1.0  # two sightings closer than this cannot form a daily window


@dataclass(frozen=True, slots=True)
class DayWindow:
    """`[as_of - older_days, as_of - newer_days)`; the name is the span, `d{newer}_{older}`."""

    newer_days: int
    older_days: int

    def __post_init__(self) -> None:
        if not 0 <= self.newer_days < self.older_days:
            raise ValueError("a window needs 0 <= newer_days < older_days")

    @property
    def name(self) -> str:
        return f"d{self.newer_days}_{self.older_days}"

    def bounds(self, as_of: datetime) -> tuple[datetime, datetime]:
        return as_of - timedelta(days=self.older_days), as_of - timedelta(days=self.newer_days)

    def contains(self, moment: datetime, *, as_of: datetime) -> bool:
        start, end = self.bounds(as_of)
        return start <= moment < end


CURRENT_D0_30: Final[DayWindow] = DayWindow(0, ARM_WINDOW_DAYS)
PRIOR_D30_60: Final[DayWindow] = DayWindow(ARM_WINDOW_DAYS, 2 * ARM_WINDOW_DAYS)
PRIOR_D30_90: Final[DayWindow] = DayWindow(ARM_WINDOW_DAYS, 3 * ARM_WINDOW_DAYS)
HISTORICAL_D90_180: Final[DayWindow] = DayWindow(3 * ARM_WINDOW_DAYS, 6 * ARM_WINDOW_DAYS)


def window_bounds(
    as_of: datetime, *, days: int, offset_windows: int = 0,
) -> tuple[datetime, datetime]:
    """Half-open `[start, end)` bounds for the `days`-long window `offset_windows` back."""
    return DayWindow(days * offset_windows, days * (offset_windows + 1)).bounds(as_of)


def growth_rate(current: float, baseline: float, *, minimum_baseline: float = 1.0) -> float | None:
    """§12's formula; `None` when the baseline cannot support a percentage."""
    if abs(baseline) < minimum_baseline:
        return None
    return (current - baseline) / max(abs(baseline), 1e-9)


def window_median_velocity(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, window: DayWindow,
) -> Tri[float]:
    """Median of in-window view deltas (§11.5) for one window, windowed only.

    Only sightings inside `window` take part, and a delta needs two sightings of the
    same entity at least a day apart. The lifetime fallback is never used: it once
    let an old channel's cumulative average outvote its own slow deltas (review fix
    5). Too few sightings for even one delta is *insufficient* — `Tri.unknown()`.
    """
    by_entity: dict[str, list[NormalizedObservation]] = {}
    for observation in observations:
        if window.contains(observation.observed_at, as_of=as_of):
            by_entity.setdefault(observation.entity_id, []).append(observation)
    velocities: list[float] = []
    for entity_id in sorted(by_entity):
        ordered = sorted(by_entity[entity_id], key=lambda o: o.observed_at)
        for earlier, later in pairwise(ordered):
            delta_days = (later.observed_at - earlier.observed_at).total_seconds() / 86400.0
            if delta_days < _SEQUENCE_GAP_DAYS:
                continue
            velocity = windowed_views_per_day(
                earlier.views, later.views, observed_at_before=earlier.observed_at,
                observed_at_after=later.observed_at,
            )
            if velocity.is_known and velocity.unwrap() >= 0:
                velocities.append(float(velocity.unwrap()))
    if not velocities:
        return Tri.unknown()
    return Tri.known(float(median(velocities)))


def median_velocity(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, window_days: int,
) -> Tri[float]:
    """§12's `median_velocity_{N}d`: the trailing window `d0_{N}`."""
    return window_median_velocity(observations, as_of=as_of, window=DayWindow(0, window_days))


def growth_between(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, current: DayWindow,
    baseline: DayWindow,
) -> Tri[float]:
    """Growth of `current` over `baseline`; unknown if either window is unmeasured."""
    if current.older_days > baseline.newer_days:
        raise ValueError(f"windows {current.name} and {baseline.name} overlap")
    now_rate = window_median_velocity(observations, as_of=as_of, window=current)
    then_rate = window_median_velocity(observations, as_of=as_of, window=baseline)
    if not (now_rate.is_known and then_rate.is_known):
        return Tri.unknown()
    rate = growth_rate(float(now_rate.unwrap()), float(then_rate.unwrap()))
    return Tri.unknown() if rate is None else Tri.known(rate)


def growth_d0_30_vs_d30_60(
    observations: Sequence[NormalizedObservation], *, as_of: datetime,
) -> Tri[float]:
    """§12: recent 30 days against the preceding comparable 30 days."""
    return growth_between(observations, as_of=as_of, current=CURRENT_D0_30,
                          baseline=PRIOR_D30_60)


def growth_d0_30_vs_d30_90(
    observations: Sequence[NormalizedObservation], *, as_of: datetime,
) -> Tri[float]:
    """Recent 30 days against the rest of the trailing quarter (days 30–90)."""
    return growth_between(observations, as_of=as_of, current=CURRENT_D0_30,
                          baseline=PRIOR_D30_90)


def growth_d0_30_vs_d90_180(
    observations: Sequence[NormalizedObservation], *, as_of: datetime,
) -> Tri[float]:
    """Recent 30 days against the prior quarter (days 90–180), the historical baseline."""
    return growth_between(observations, as_of=as_of, current=CURRENT_D0_30,
                          baseline=HISTORICAL_D90_180)


def median_recent_velocity(
    observations: Sequence[NormalizedObservation], *, now: datetime, window_hours: float,
    min_span_fraction: float,
) -> Tri[float]:
    """Arm-level 24h/72h velocity: the median of each entity's `recent_views_per_day`.

    An entity without enough sightings in the window does not vote; an arm where no
    entity can vote is unknown.
    """
    by_entity: dict[str, list[tuple[datetime, Tri[float]]]] = {}
    for observation in observations:
        by_entity.setdefault(observation.entity_id, []).append(
            (observation.observed_at, observation.views)
        )
    velocities: list[float] = []
    for entity_id in sorted(by_entity):
        velocity = recent_views_per_day(by_entity[entity_id], now=now,
                                        window_hours=window_hours,
                                        min_span_fraction=min_span_fraction)
        if velocity.is_known:
            velocities.append(float(velocity.unwrap()))
    if not velocities:
        return Tri.unknown()
    return Tri.known(float(median(velocities)))

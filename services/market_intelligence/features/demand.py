# services/market_intelligence/features/demand.py
"""§17.3 demand score inputs — a size-independent *pull* measure.

Demand must not rank raw audience size. The architecture is explicit: "Raw view
count is not sufficient. A small channel with unusually fast growth can be a more
useful signal than a very large established channel with slow growth", and §35's
fixture exists so that the 10M-subscriber channel does not win by construction (it
scored 100 on demand when this input was the median of raw cumulative views).

Choice (review fix 6): each in-window sighting contributes a **per-subscriber view
rate** — its lifetime views per day relative to the current subscriber count
(§11.2's rate over §11.3's ratio: `views / age_days / max(subscribers, 1)`) — and the
demand input is the median of those rates. This is a size-independent measure: a
mega channel's cumulative totals are divided by both its age and its audience, so
what remains is attention *earned per subscriber*, not bigness. A sighting without
views, subscribers or a publication age cannot produce a rate (§11.3 marks the
signal unstable) and simply does not vote.

`active_channel_audience` stays on the object as reported context only. It is raw
size by another name: it is deliberately *not* composited into the demand score,
for the same reason the raw view median is not. `query_prevalence` and
`recent_activity_count` are the remaining §17.3 demand elements; only the activity
count (cadence) is size-independent enough to join the pull rate in the score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation
from .velocity import lifetime_views_per_day

__all__ = ["DemandInputs", "demand_inputs"]


@dataclass(frozen=True, slots=True)
class DemandInputs:
    median_pull_per_day: Tri
    query_prevalence: Tri
    active_channel_audience: Tri
    recent_activity_count: int


def demand_inputs(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, window_days: int,
) -> DemandInputs:
    """Everything the demand component needs; missing pieces stay unknown (§3.6)."""
    recent = [o for o in observations
              if (as_of - o.observed_at).total_seconds() <= window_days * 86400]
    pulls: list[float] = []
    for observation in recent:
        if (observation.published_at is None or not observation.views.is_known
                or not observation.subscriber_count.is_known):
            continue
        velocity = lifetime_views_per_day(
            observation.views, observed_at=observation.observed_at,
            published_at=observation.published_at,
        )
        subscribers = float(observation.subscriber_count.unwrap())
        if velocity.is_known and subscribers > 0:
            pulls.append(float(velocity.unwrap()) / subscribers)
    pull: Tri = Tri.known(median(pulls)) if pulls else Tri.unknown()
    audiences = [float(o.subscriber_count.unwrap()) for o in recent
                 if o.subscriber_count.is_known]
    return DemandInputs(
        median_pull_per_day=pull,
        query_prevalence=Tri.known(float(len(recent))) if recent else Tri.unknown(),
        active_channel_audience=Tri.known(sum(audiences)) if audiences else Tri.unknown(),
        recent_activity_count=len(recent),
    )

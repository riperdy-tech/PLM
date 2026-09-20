# services/market_intelligence/features/demand.py
"""§17.3 demand score inputs — composites of evidence, each Tri-valued."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation

__all__ = ["DemandInputs", "demand_inputs"]


@dataclass(frozen=True, slots=True)
class DemandInputs:
    median_views_per_day: Tri
    query_prevalence: Tri
    active_channel_audience: Tri
    recent_activity_count: int


def demand_inputs(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, window_days: int,
) -> DemandInputs:
    """Everything the demand component needs; missing pieces stay unknown (§3.6)."""
    recent = [o for o in observations
              if (as_of - o.observed_at).total_seconds() <= window_days * 86400]
    velocities = sorted(
        float(o.views.unwrap()) for o in recent if o.views.is_known
    )
    median: Tri = Tri.unknown()
    if velocities:
        middle = len(velocities) // 2
        median = Tri.known(velocities[middle] if len(velocities) % 2
                           else (velocities[middle - 1] + velocities[middle]) / 2)
    audiences = [float(o.subscriber_count.unwrap()) for o in recent
                 if o.subscriber_count.is_known]
    return DemandInputs(
        median_views_per_day=median,
        query_prevalence=Tri.known(float(len(recent))) if recent else Tri.unknown(),
        active_channel_audience=Tri.known(sum(audiences)) if audiences else Tri.unknown(),
        recent_activity_count=len(recent),
    )

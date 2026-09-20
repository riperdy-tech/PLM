# services/market_intelligence/features/cross_platform.py
"""§17.3 cross-platform score inputs: secondary sources only, never YouTube metrics."""

from __future__ import annotations

from collections.abc import Sequence

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation
from ..domain.enums import ProviderName
from .velocity import lifetime_views_per_day

__all__ = ["cross_platform_signal"]

_SECONDARY_SOURCES = frozenset({ProviderName.SPOTIFY_WEB_API.value, ProviderName.PUBLIC_TRENDS.value})


def cross_platform_signal(secondary_observations: Sequence[NormalizedObservation]) -> Tri:
    """Mean normalised engagement across secondary sources; unknown with none of them.

    An unavailable secondary source reduces confidence (P1.8's breakdown) — it never
    contributes a zero (§17.3).
    """
    values: list[float] = []
    for observation in secondary_observations:
        if observation.source_id in _SECONDARY_SOURCES and observation.views.is_known:
            velocity = lifetime_views_per_day(
                observation.views, observed_at=observation.observed_at,
                published_at=observation.published_at or observation.observed_at,
            )
            values.append(float(velocity.unwrap()))
    if not values:
        return Tri.unknown()
    return Tri.known(sum(values) / len(values))

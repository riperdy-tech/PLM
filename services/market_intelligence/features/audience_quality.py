# services/market_intelligence/features/audience_quality.py
"""§17.3 audience-quality inputs, all from observable public signals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation
from .velocity import like_rate

__all__ = ["AudienceQualityInputs", "audience_quality_inputs"]

_LONG_FORM_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class AudienceQualityInputs:
    long_form_share: Tri[float]
    engagement_consistency: Tri[float]
    international_spread: Tri[float]
    repeat_playlist_proxy: Tri[float]


def audience_quality_inputs(observations: Sequence[NormalizedObservation]) -> AudienceQualityInputs:
    durations = [float(o.video_duration_seconds.unwrap()) for o in observations
                 if o.video_duration_seconds.is_known]
    long_form = Tri.known(sum(1 for d in durations if d >= _LONG_FORM_SECONDS) / len(durations)) \
        if durations else Tri.unknown()
    rates = [float(like_rate(o.likes, o.views).unwrap()) for o in observations
             if like_rate(o.likes, o.views).is_known]
    consistency: Tri[float] = Tri.unknown()
    if len(rates) >= 2:
        spread = max(rates) - min(rates)
        consistency = Tri.known(max(0.0, 1.0 - spread))
    countries = {o.country_signal for o in observations if o.country_signal}
    international: Tri[float] = (Tri.known(min(1.0, len(countries) / 3.0))
                                 if countries else Tri.unknown())
    # A proxy backed by zero sightings of the metric is *unknown*, not a measured
    # zero: the plan's `known(0/len)` would render unobserved data as data.
    with_known_playlist = sum(1 for o in observations if o.playlist_item_count.is_known)
    playlist_proxy = Tri.known(with_known_playlist / len(observations)) \
        if observations and with_known_playlist else Tri.unknown()
    return AudienceQualityInputs(long_form_share=long_form, engagement_consistency=consistency,
                                 international_spread=international,
                                 repeat_playlist_proxy=playlist_proxy)

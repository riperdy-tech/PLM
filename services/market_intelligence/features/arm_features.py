# services/market_intelligence/features/arm_features.py
"""Arm-level feature assembly: the single place observations become numbers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from plm_contracts import Tri

from ..config import MarketSettings
from ..domain.entities import NormalizedObservation
from ..domain.value_objects import FreshnessAssessment, TaxonomyProfile, classify_freshness
from .audience_quality import AudienceQualityInputs, audience_quality_inputs
from .competition import ArmCompetition, competition_metrics
from .confidence import ConfidenceBreakdown, confidence
from .cross_platform import cross_platform_signal
from .demand import DemandInputs, demand_inputs
from .growth import (
    growth_d0_30_vs_d30_60,
    growth_d0_30_vs_d30_90,
    growth_d0_30_vs_d90_180,
    median_recent_velocity,
    median_velocity,
)
from .production_fit import ProductionFitInputs, production_fit_inputs
from .velocity import lifetime_views_per_day

__all__ = [
    "VELOCITY_24H_HOURS", "VELOCITY_72H_HOURS", "ArmFeatures", "arm_competition",
    "compute_arm_features", "features_freshness",
]

_COMPETITION_WINDOW_DAYS = 30
# Short-horizon velocity windows (§9's 24h/72h cadences). The feature names state the
# span, so the spans are fixed here rather than configured under a misleading name.
VELOCITY_24H_HOURS = 24.0
VELOCITY_72H_HOURS = 72.0


@dataclass(frozen=True, slots=True)
class ArmFeatures:
    arm_id: str
    fingerprint: str
    as_of: datetime
    observation_count: int
    source_count: int
    observation_ids: tuple[str, ...]
    median_velocity_24h: Tri[float]
    median_velocity_72h: Tri[float]
    median_velocity_7d: Tri[float]
    median_velocity_30d: Tri[float]
    median_velocity_90d: Tri[float]
    growth_d0_30_vs_d30_60: Tri[float]
    growth_d0_30_vs_d30_90: Tri[float]
    growth_d0_30_vs_d90_180: Tri[float]
    competition: ArmCompetition
    demand: DemandInputs
    audience: AudienceQualityInputs
    cross_platform: Tri[float]
    production_fit: ProductionFitInputs
    confidence_breakdown: ConfidenceBreakdown

    @property
    def confidence(self) -> float:
        return confidence(self.confidence_breakdown)


def features_freshness(observations: Sequence[NormalizedObservation], *, now: datetime,
                       settings: MarketSettings) -> FreshnessAssessment:
    """§21: freshness of the newest observation behind the features."""
    newest = max((o.observed_at for o in observations), default=None)
    if newest is None:
        return classify_freshness(as_of=now, now=now, fresh_hours=settings.freshness_fresh_hours,
                                  normal_hours=settings.freshness_normal_hours,
                                  stale_hours=settings.freshness_stale_hours)
    return classify_freshness(
        as_of=newest, now=now, fresh_hours=settings.freshness_fresh_hours,
        normal_hours=settings.freshness_normal_hours,
        stale_hours=settings.freshness_stale_hours,
    )


def arm_competition(
    observations: Sequence[NormalizedObservation], *, as_of: datetime,
) -> ArmCompetition:
    """§13 for one arm: one vote per video, activity attributed to its channel.

    Each video contributes its newest sighting with a measurable velocity, so a
    video sighted five times is not five competitors. Its channel is the newest
    channel id any of its sightings names. Concentration (HHI) is over channels:
    two videos from one channel are one channel with all the activity. If any active
    video has no channel, attribution is incomplete and the channel-keyed metrics
    are unknown — they are never computed over video ids.
    """
    by_video: dict[str, list[NormalizedObservation]] = {}
    for observation in observations:
        by_video.setdefault(observation.entity_id, []).append(observation)
    video_velocities: list[float] = []
    channel_activity: dict[str, float] = {}
    channel_size: dict[str, tuple[datetime, float]] = {}
    attributable = True
    uploads = 0
    for video_id in sorted(by_video):
        sightings = sorted(by_video[video_id], key=lambda o: o.observed_at)
        channel_id = next((o.channel_id for o in reversed(sightings) if o.channel_id), None)
        for sighting in sightings:
            if channel_id is not None and sighting.subscriber_count.is_known:
                previous = channel_size.get(channel_id)
                if previous is None or sighting.observed_at >= previous[0]:
                    channel_size[channel_id] = (
                        sighting.observed_at, float(sighting.subscriber_count.unwrap()),
                    )
        published = next((o.published_at for o in sightings if o.published_at is not None),
                          None)
        if published is not None and (
            (as_of - published).total_seconds() <= _COMPETITION_WINDOW_DAYS * 86400
        ):
            uploads += 1
        velocity: Tri[float] = Tri.unknown()
        for sighting in reversed(sightings):
            if sighting.published_at is not None and sighting.views.is_known:
                velocity = lifetime_views_per_day(
                    sighting.views, observed_at=sighting.observed_at,
                    published_at=sighting.published_at,
                )
                break
        if not velocity.is_known:
            continue
        video_velocities.append(float(velocity.unwrap()))
        if channel_id is None:
            attributable = False
            continue
        channel_activity[channel_id] = (
            channel_activity.get(channel_id, 0.0) + float(velocity.unwrap())
        )
    return competition_metrics(
        video_velocities=video_velocities,
        channel_sizes=[size for _at, size in channel_size.values()],
        channel_activities=[channel_activity[key] for key in sorted(channel_activity)]
        if attributable else None,
        uploads_last_window=uploads, window_days=_COMPETITION_WINDOW_DAYS,
    )


def compute_arm_features(
    observations_by_arm: Mapping[str, Sequence[NormalizedObservation]],
    *,
    as_of: datetime,
    settings: MarketSettings,
    arm_ids: Mapping[str, str] | None = None,
    profiles: Mapping[str, TaxonomyProfile] | None = None,
    taxonomy_confidence: Mapping[str, float] | None = None,
) -> dict[str, ArmFeatures]:
    """`observations_by_arm` is keyed by arm fingerprint (P1.9 owns the identity)."""
    features: dict[str, ArmFeatures] = {}
    for arm_fingerprint, observations in observations_by_arm.items():
        competition = arm_competition(observations, as_of=as_of)
        freshness = features_freshness(observations, now=as_of, settings=settings)
        sources = {observation.source_id for observation in observations}
        series_depth = _series_depth(observations)
        taxonomy = (taxonomy_confidence or {}).get(arm_fingerprint, 0.0)
        breakdown = ConfidenceBreakdown(
            sample=min(1.0, len(observations) / max(settings.confidence_sample_target, 1)),
            source_diversity=min(1.0, len(sources) / max(settings.confidence_source_target, 1)),
            time_series_depth=series_depth,
            taxonomy=taxonomy,
            freshness={"fresh": 1.0, "normal": 0.75, "stale": 0.35, "expired": 0.0}[
                freshness.bucket.value
            ],
        )
        profile = (profiles or {}).get(arm_fingerprint) or TaxonomyProfile()
        features[arm_fingerprint] = ArmFeatures(
            arm_id=(arm_ids or {}).get(arm_fingerprint, arm_fingerprint),
            fingerprint=arm_fingerprint, as_of=as_of,
            observation_count=len(observations), source_count=len(sources),
            observation_ids=tuple(o.raw_observation_id for o in observations),
            median_velocity_24h=median_recent_velocity(
                observations, now=as_of, window_hours=VELOCITY_24H_HOURS,
                min_span_fraction=settings.velocity_min_span_fraction,
            ),
            median_velocity_72h=median_recent_velocity(
                observations, now=as_of, window_hours=VELOCITY_72H_HOURS,
                min_span_fraction=settings.velocity_min_span_fraction,
            ),
            median_velocity_7d=median_velocity(observations, as_of=as_of, window_days=7),
            median_velocity_30d=median_velocity(observations, as_of=as_of, window_days=30),
            median_velocity_90d=median_velocity(observations, as_of=as_of, window_days=90),
            growth_d0_30_vs_d30_60=growth_d0_30_vs_d30_60(observations, as_of=as_of),
            growth_d0_30_vs_d30_90=growth_d0_30_vs_d30_90(observations, as_of=as_of),
            growth_d0_30_vs_d90_180=growth_d0_30_vs_d90_180(observations, as_of=as_of),
            competition=competition, demand=demand_inputs(observations, as_of=as_of,
                                                          window_days=30),
            audience=audience_quality_inputs(observations),
            cross_platform=cross_platform_signal(observations),
            production_fit=production_fit_inputs(profile, settings),
            confidence_breakdown=breakdown,
        )
    return features


def _series_depth(observations: Sequence[NormalizedObservation]) -> float:
    """Share of entities with at least two sightings — §19's time-series depth."""
    if not observations:
        return 0.0
    counts: dict[str, int] = {}
    for observation in observations:
        counts[observation.entity_id] = counts.get(observation.entity_id, 0) + 1
    return sum(1 for count in counts.values() if count >= 2) / len(counts)

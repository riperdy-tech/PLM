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
from .growth import growth_30d_vs_prev30d, growth_30d_vs_prev90d, median_velocity
from .production_fit import ProductionFitInputs, production_fit_inputs
from .velocity import lifetime_views_per_day

__all__ = ["ArmFeatures", "compute_arm_features", "features_freshness"]


@dataclass(frozen=True, slots=True)
class ArmFeatures:
    arm_id: str
    fingerprint: str
    as_of: datetime
    observation_count: int
    source_count: int
    observation_ids: tuple[str, ...]
    median_velocity_7d: Tri
    median_velocity_30d: Tri
    median_velocity_90d: Tri
    growth_30d_vs_prev30d: Tri
    growth_30d_vs_prev90d: Tri
    competition: ArmCompetition
    demand: DemandInputs
    audience: AudienceQualityInputs
    cross_platform: Tri
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
        velocities: list[float] = []
        channel_sizes: list[float] = []
        channel_activity: dict[str, float] = {}
        uploads = 0
        for observation in observations:
            if observation.published_at is not None and observation.views.is_known:
                velocity = lifetime_views_per_day(
                    observation.views, observed_at=observation.observed_at,
                    published_at=observation.published_at,
                )
                if velocity.is_known:
                    velocities.append(float(velocity.unwrap()))
                    channel_activity[observation.entity_id] = (
                        channel_activity.get(observation.entity_id, 0.0)
                        + float(velocity.unwrap())
                    )
            if observation.subscriber_count.is_known:
                channel_sizes.append(float(observation.subscriber_count.unwrap()))
            if observation.published_at is not None and (
                (as_of - observation.published_at).total_seconds() <= 30 * 86400
            ):
                uploads += 1
        competition = competition_metrics(
            video_velocities=velocities, channel_sizes=channel_sizes,
            channel_activities=list(channel_activity.values()),
            uploads_last_window=uploads, window_days=30,
        )
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
            median_velocity_7d=median_velocity(observations, as_of=as_of, window_days=7),
            median_velocity_30d=median_velocity(observations, as_of=as_of, window_days=30),
            median_velocity_90d=median_velocity(observations, as_of=as_of, window_days=90),
            growth_30d_vs_prev30d=growth_30d_vs_prev30d(observations, as_of=as_of,
                                                        settings=settings),
            growth_30d_vs_prev90d=growth_30d_vs_prev90d(observations, as_of=as_of,
                                                        settings=settings),
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

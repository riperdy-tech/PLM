# services/market_intelligence/scoring/opportunity.py
"""§17–§20: the versioned, deterministic opportunity score.

Determinism (§30): every input is passed in — features, weights, versions, `as_of`.
There is no clock access, no randomness and no LLM call in this module. Given the same
stored observations and the same versions this produces byte-identical output.

Missing data (the P1.8/P1.9 brief's first rule): the seven components are `Tri`-valued.
An unobserved input leaves its component `unknown` rather than a measured 0.0, and the
weighted average renormalises over the components that *were* observed, so a missing
metric is neither a fabricated zero nor a passing value. An arm with no observed
evidence component gets no `OpportunityScore` at all — `production_fit` is
configuration, not evidence, so it cannot carry a score by itself (§3.6, §35).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from plm_contracts import Tri

from ..config import MarketSettings
from ..domain.entities import OpportunityScore
from ..domain.enums import ScoringComponent
from ..domain.value_objects import EvidenceSummary, ScoreComponents
from ..features.arm_features import ArmFeatures
from .calibration import SCORE_VERSION, ScoreWeights, default_weights
from .normalization import percentile_score

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "ArmComponentScores",
    "compute_component_scores",
    "persist_scores",
    "score_all_arms",
    "score_arm",
]

EVIDENCE_COMPONENTS: Final[tuple[ScoringComponent, ...]] = (
    ScoringComponent.DEMAND,
    ScoringComponent.GROWTH,
    ScoringComponent.COMPETITOR_VELOCITY,
    ScoringComponent.COMPETITION,
    ScoringComponent.AUDIENCE_QUALITY,
    ScoringComponent.CROSS_PLATFORM,
)
"""§17.3's observed components; `production_fit` is config-driven feasibility, not evidence."""


@dataclass(frozen=True, slots=True)
class ArmComponentScores:
    """§17.2's seven components as Tri: `unknown` = unobserved, never a measured zero."""

    demand: Tri[float]
    growth: Tri[float]
    competitor_velocity: Tri[float]
    competition: Tri[float]
    audience_quality: Tri[float]
    cross_platform: Tri[float]
    production_fit: Tri[float]

    def as_mapping(self) -> dict[ScoringComponent, Tri[float]]:
        return {component: getattr(self, component.value) for component in ScoringComponent}

    @property
    def has_evidence(self) -> bool:
        """Scoring basis: at least one *observed* evidence component (§3.6, §35)."""
        return any(getattr(self, component.value).is_known for component in EVIDENCE_COMPONENTS)


def _demand_value(features: ArmFeatures) -> float | None:
    """§17.3/§35: demand measures *pull*, not bigness (review fix 6).

    The input is the median per-subscriber view rate — size-independent by
    construction — plus the arm's recent activity count (cadence). Raw cumulative
    views and the summed subscriber base are deliberately absent: compositing them
    made demand a raw-popularity rank (slow_mega scored 100 on nothing but size),
    exactly what the architecture rules out and §35's fixture exists to catch.
    """
    if not features.demand.median_pull_per_day.is_known:
        return None
    return (float(features.demand.median_pull_per_day.unwrap())
            + features.demand.recent_activity_count)


def _growth_value(features: ArmFeatures) -> float | None:
    if not features.growth_30d_vs_prev30d.is_known:
        return None
    return float(features.growth_30d_vs_prev30d.unwrap())


def _competitor_velocity_value(features: ArmFeatures) -> float | None:
    """§17.3: how strongly competitors are *growing* — the recent windowed velocity.

    The plan ranked `competition.median_video_velocity`, a lifetime average that grows
    with a channel's age and size; §35's fixture exists so that raw size does not
    decide the ranking, and the arm's §12 30-day velocity is the measure §17.3
    describes (windowed deltas where sightings allow, lifetime only as the fallback).
    """
    if not features.median_velocity_30d.is_known:
        return None
    return float(features.median_velocity_30d.unwrap())


def _competition_penalty_value(features: ArmFeatures) -> float | None:
    """What must *not* help: saturation and concentration, both higher-is-worse."""
    if not features.competition.sample_size:
        return None
    return (features.competition.concentration_normalized * 100.0
            + features.competition.top_10_channel_share * 100.0)


def _audience_value(features: ArmFeatures) -> float | None:
    components = [features.audience.long_form_share, features.audience.engagement_consistency,
                  features.audience.international_spread]
    known = [float(value.unwrap()) for value in components if value.is_known]
    return sum(known) / len(known) if known else None


def _cross_platform_value(features: ArmFeatures) -> float | None:
    return float(features.cross_platform.unwrap()) if features.cross_platform.is_known else None


def _production_fit_value(features: ArmFeatures) -> float:
    fit = features.production_fit
    return (fit.vocal_compatibility + fit.language_support + fit.visual_feasibility
            + fit.copyright_risk_ease) / 4.0


_EXTRACTORS: Final[Mapping[ScoringComponent, Callable[[ArmFeatures], float | None]]] = {
    ScoringComponent.DEMAND: _demand_value,
    ScoringComponent.GROWTH: _growth_value,
    ScoringComponent.COMPETITOR_VELOCITY: _competitor_velocity_value,
    ScoringComponent.COMPETITION: _competition_penalty_value,
    ScoringComponent.AUDIENCE_QUALITY: _audience_value,
    ScoringComponent.CROSS_PLATFORM: _cross_platform_value,
    ScoringComponent.PRODUCTION_FIT: _production_fit_value,
}


def compute_component_scores(
    features_by_arm: Mapping[str, ArmFeatures], *, settings: MarketSettings,
) -> dict[str, ArmComponentScores]:
    """Rank every *scorable* arm against the arm population, component by component.

    An unobserved input leaves the component `unknown` — no rank floor is fabricated.
    Arms with no observed evidence component are omitted entirely: §3.6 says preserve
    the metric rather than manufacture certainty, so such an arm has *no* score.
    """
    if not features_by_arm:
        return {}
    raw_values: dict[str, dict[ScoringComponent, float | None]] = {
        arm: {component: _EXTRACTORS[component](features) for component in ScoringComponent}
        for arm, features in features_by_arm.items()
    }
    populations: dict[ScoringComponent, list[float]] = {
        component: [value for values in raw_values.values()
                    if (value := values[component]) is not None]
        for component in ScoringComponent
    }
    scores: dict[str, ArmComponentScores] = {}
    for arm, values in raw_values.items():
        components: dict[str, Tri[float]] = {}
        for component in ScoringComponent:
            value = values[component]
            if value is None:
                components[component.value] = Tri.unknown()
                continue
            rank = percentile_score(value, population=populations[component])
            if component is ScoringComponent.COMPETITION:
                # §17.3: lower saturation contributes *more* opportunity, so invert it
                rank = 100.0 - rank
            components[component.value] = Tri.known(rank)
        entry = ArmComponentScores(**components)
        if entry.has_evidence:
            scores[arm] = entry
    return scores


def score_arm(
    features: ArmFeatures,
    components: ArmComponentScores | ScoreComponents,
    *,
    as_of: datetime,
    weights: ScoreWeights,
    score_version: str = SCORE_VERSION,
    feature_version: str,
    normalization_version: str,
) -> OpportunityScore:
    """The weighted average of the *measured* components, clamped to 0–100.

    An unobserved component carries no weight: the average renormalises over the
    measured ones, so missing data is neither a zero nor a pass. A component set with
    nothing measured cannot be scored and is refused — `compute_component_scores`
    filters those arms out before this is called.
    """
    weights.validate()
    weight_by_component = weights.as_mapping()
    measured: dict[ScoringComponent, float] = {}
    for component, raw in components.as_mapping().items():
        tri = raw if isinstance(raw, Tri) else Tri.known(float(raw))
        if tri.is_known:
            measured[component] = float(tri.unwrap())
    total_weight = sum(weight_by_component[component.value] for component in measured)
    evidence_measured = any(
        component in measured for component in EVIDENCE_COMPONENTS
    )
    if not evidence_measured or total_weight <= 0:
        # §3.6/§35: `production_fit` is configuration, not market evidence. A measured
        # production_fit used to satisfy a `total_weight > 0` guard and the arm got a
        # score from no evidence at all; an arm without one observed evidence
        # component gets no score, whatever the configuration says.
        raise ValueError(
            "no measured components: an arm without evidence gets no score; "
            "production_fit is configuration and cannot carry a score by itself"
        )
    weighted = sum(measured[component] * weight_by_component[component.value]
                   for component in measured) / total_weight
    score = max(0.0, min(100.0, weighted))
    return OpportunityScore(
        arm_id=features.arm_id,
        market_opportunity_score=score,
        confidence=features.confidence,
        basis_weight=total_weight,
        components=components,
        evidence=EvidenceSummary(
            observation_count=features.observation_count,
            source_count=features.source_count,
            observation_ids=features.observation_ids,
        ),
        as_of=as_of,
        score_version=score_version,
        feature_version=feature_version,
        normalization_version=normalization_version,
    )


def score_all_arms(
    features_by_arm: Mapping[str, ArmFeatures], *, as_of: datetime, settings: MarketSettings,
    weights: ScoreWeights | None = None,
) -> dict[str, OpportunityScore]:
    active_weights = default_weights() if weights is None else weights
    components_by_arm = compute_component_scores(features_by_arm, settings=settings)
    return {
        arm_fingerprint: score_arm(
            features_by_arm[arm_fingerprint], components, as_of=as_of, weights=active_weights,
            score_version=settings.score_version, feature_version=settings.feature_version,
            normalization_version=settings.normalization_version,
        )
        for arm_fingerprint, components in components_by_arm.items()
    }


def persist_scores(
    session: Session,
    scores: Mapping[str, OpportunityScore],
    *,
    computed_at: datetime,
) -> int:
    """Append one score row per arm per computation instant (§3.1/§20).

    Every persisted value comes off the score object — including all three versions
    and the full input set (review fix 2): the row's claim about how it was produced
    cannot drift from the computation that produced it.
    """
    from ..storage.repositories import insert_score

    written = 0
    for score in scores.values():
        insert_score(
            session, arm_id=score.arm_id, score=score, computed_at=computed_at,
            components={component.value: _component_json(value)
                        for component, value in score.components.as_mapping().items()},
            evidence={"observation_count": score.evidence.observation_count,
                      "source_count": score.evidence.source_count,
                      "top_observation_ids": list(score.evidence.top_observation_ids),
                      "observation_ids": list(score.evidence.observation_ids)},
        )
        written += 1
    return written


def _component_json(value: Any) -> dict[str, Any] | float:
    """Unknown components persist in the tagged Tri form, never as a numeric zero."""
    if isinstance(value, Tri):
        return value.to_json_value()
    return float(value)

# services/autonomous_ar/modeling/prior.py
"""Cold-start prior (§10) and bounded transfer learning (§64/§65), versioned `p2-prior-v1`.

The transform is deterministic once the version string is fixed. Module constants are the
transform's parameters — they move only with `prior_version`, never silently.
"""

from __future__ import annotations

from ..config import ARSettings
from ..domain.arm import ArmDimensions, ContentArm
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState, PriorParameters
from .similarity import structural_overlap

__all__ = ["cold_start_prior", "transfer_contribution"]

BASE_VARIANCE = 0.04
"""Variance at perfect confidence, fresh data — the transform's floor for a new arm."""

CONFIDENCE_SPREAD = 0.16
"""Extra variance at zero confidence: `(1 − c)²` scaled by this."""

TRANSFER_HALFSAT = 10.0
"""Observations at which a neighbour is 'half-proven' for transfer weighting."""


def cold_start_prior(arm: ContentArm, *, settings: ARSettings,
                     policy: PolicyConfig) -> PriorParameters:
    """P1 evidence → prior mean/variance; direction follows the score, width follows
    confidence and staleness (§10, §83). No first-party performance is fabricated."""
    base = arm.p1_score / 100.0
    confidence = arm.p1_confidence
    mean = min(0.95, max(0.05, 0.5 + (base - 0.5) * confidence))
    max_age_seconds = policy.max_market_data_age_hours * 3600.0
    staleness = min(1.0, arm.freshness_seconds / max_age_seconds)
    variance = BASE_VARIANCE + CONFIDENCE_SPREAD * (1.0 - confidence) ** 2 \
        + BASE_VARIANCE * staleness
    return PriorParameters(mean=mean, variance=variance, confidence=confidence,
                           prior_version=settings.prior_version)


def transfer_contribution(prior: PriorParameters, *, neighbour: PosteriorState,
                          similarity: float, neighbour_dims: ArmDimensions,
                          arm_dims: ArmDimensions, settings: ARSettings) -> PriorParameters:
    """Blend in a measured neighbour's posterior, bounded by structure and evidence.

    §65: structural similarity is checked first (`structural_overlap`); free-form text is
    never consulted. §64: the weight is capped at `transfer_max_weight`, so a new arm never
    inherits the proven arm's certainty.
    """
    if structural_overlap(arm_dims, neighbour_dims) < settings.min_transfer_shared_dimensions:
        return prior
    if similarity < settings.similarity_threshold:
        return prior
    if neighbour.effective_observation_count <= 0.0:
        return prior
    provenness = neighbour.effective_observation_count / (
        neighbour.effective_observation_count + TRANSFER_HALFSAT
    )
    weight = settings.transfer_max_weight * similarity * provenness
    if weight <= 0.0:
        return prior
    mean = (1.0 - weight) * prior.mean + weight * neighbour.posterior_mean
    gap = (prior.mean - neighbour.posterior_mean) ** 2
    variance = ((1.0 - weight) * prior.variance + weight * neighbour.posterior_variance
                + weight * (1.0 - weight) * gap)  # law of total variance: never certain
    return PriorParameters(mean=mean, variance=variance, confidence=prior.confidence,
                           prior_version=prior.prior_version)

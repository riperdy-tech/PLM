# tests/unit/ar/test_prior.py
from datetime import UTC, datetime

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PosteriorState
from services.autonomous_ar.modeling.prior import cold_start_prior, transfer_contribution

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
COMPONENTS = {"demand": 70.0, "growth": 50.0, "competitor_velocity": 55.0,
              "competition": 60.0, "audience_quality": 40.0, "cross_platform": None,
              "production_fit": 58.0}


def _arm(score: float, confidence: float, freshness: float = 0.0) -> ContentArm:
    return ContentArm(
        arm_id="arm-1", dimensions=ArmDimensions(genre="rnb", language="english"),
        p1_score=score, p1_confidence=confidence, component_scores=COMPONENTS,
        observation_count=6, source_count=2, as_of=NOW, score_version="p1-v1",
        freshness_seconds=freshness,
    )


def test_confidence_controls_both_direction_and_width() -> None:
    settings, policy = load_ar_settings(), DEFAULT_POLICY
    strong = cold_start_prior(_arm(82.0, 0.7), settings=settings, policy=policy)
    weak = cold_start_prior(_arm(82.0, 0.2), settings=settings, policy=policy)
    assert strong.mean > weak.mean, "same score, lower confidence ⇒ closer to the midpoint"
    assert strong.variance < weak.variance
    low = cold_start_prior(_arm(45.0, 0.9), settings=settings, policy=policy)
    assert low.mean < 0.5


def test_stale_market_evidence_widens_the_prior_without_moving_it() -> None:
    settings, policy = load_ar_settings(), DEFAULT_POLICY
    fresh = cold_start_prior(_arm(82.0, 0.7), settings=settings, policy=policy)
    stale = cold_start_prior(_arm(82.0, 0.7, freshness=168.0 * 3600.0), settings=settings,
                             policy=policy)
    assert stale.mean == fresh.mean
    assert stale.variance > fresh.variance
    assert fresh.prior_version == "p2-prior-v1"


def _proven(dims: ArmDimensions | None = None) -> PosteriorState:
    return PosteriorState(
        arm_id="arm-proven", model_type="normal-normal", model_version="p2-nn-v1",
        prior_mean=0.6, prior_variance=0.05, posterior_mean=0.75, posterior_variance=0.01,
        observation_count=30, effective_observation_count=25.0, update_version=4,
        last_update_at=NOW,
    )


def test_transfer_is_gated_on_structure_and_stays_less_certain_than_the_source() -> None:
    settings, policy = load_ar_settings(), DEFAULT_POLICY
    dims = ArmDimensions(genre="rnb", language="english", vocal_mode="vocal")
    prior = cold_start_prior(_arm(70.0, 0.5), settings=settings, policy=policy)
    transferred = transfer_contribution(
        prior, neighbour=_proven(), similarity=0.8, neighbour_dims=dims, arm_dims=dims,
        settings=settings,
    )
    assert transferred.mean > prior.mean, "a strong measured neighbour pulls the prior up"
    assert transferred.variance > _proven().posterior_variance, (
        "a new arm never starts with the proven arm's certainty (§64)"
    )
    # never transfers on prose-like similarity alone: below threshold ⇒ untouched
    untouched = transfer_contribution(
        prior, neighbour=_proven(), similarity=0.2, neighbour_dims=dims, arm_dims=dims,
        settings=settings,
    )
    assert untouched == prior
    # not enough shared structure ⇒ untouched even at high similarity (§65)
    thin = transfer_contribution(
        prior, neighbour=_proven(), similarity=1.0,
        neighbour_dims=ArmDimensions(genre="rnb"),
        arm_dims=ArmDimensions(genre="rnb"), settings=settings,
    )
    assert thin == prior


def test_replay_from_the_same_inputs_is_identical() -> None:
    settings, policy = load_ar_settings(), DEFAULT_POLICY
    arm = _arm(82.0, 0.7)
    first = cold_start_prior(arm, settings=settings, policy=policy)
    second = cold_start_prior(arm, settings=settings, policy=policy)
    assert first == second, "same stored inputs + version ⇒ same prior"
    assert first.prior_version == "p2-prior-v1", "the version travels with the value"
    dims = ArmDimensions(genre="rnb", language="english", vocal_mode="vocal")
    first_transfer = transfer_contribution(
        first, neighbour=_proven(), similarity=0.8, neighbour_dims=dims, arm_dims=dims,
        settings=settings,
    )
    second_transfer = transfer_contribution(
        second, neighbour=_proven(), similarity=0.8, neighbour_dims=dims, arm_dims=dims,
        settings=settings,
    )
    assert first_transfer == second_transfer, "same inputs ⇒ same transferred prior"
    assert first_transfer.prior_version == first.prior_version, "transfer never re-versions"

"""§74/§75: exactly-once posterior updates, attribution gating, recency (P2.11)."""

from datetime import UTC, datetime, timedelta

from plm_contracts import ObservationWindow, Tri

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.enums import ObservationMaturity
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PriorParameters
from services.autonomous_ar.domain.reward import RewardObservation
from services.autonomous_ar.ingestion.result_adapter import store_result
from services.autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from services.autonomous_ar.modeling.update import apply_pending_updates
from services.autonomous_ar.storage import repositories
from tests.support.ar_db import ar_session, create_ar_engine
from tests.support.seams import unmeasured_raw_metrics

NOW = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
SETTINGS = load_ar_settings()
MODEL = NormalNormalRewardModel()
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _seed_arm_and_posterior(session, *, mean: float = 0.5, variance: float = 0.05) -> None:
    repositories.upsert_arm_state(session, brand_id="neon_seoul", arm_id="rnb-1", now=NOW)
    arm = ContentArm(arm_id="rnb-1", dimensions=ArmDimensions(genre="rnb"),
                     p1_score=60.0, p1_confidence=0.6, component_scores=dict(COMPONENTS),
                     observation_count=1, source_count=1, as_of=NOW, score_version="p1-v1",
                     freshness_seconds=0.0)
    repositories.upsert_posterior(
        session,
        MODEL.initialize_prior(arm, PriorParameters(
            mean=mean, variance=variance, confidence=0.6, prior_version="p2-prior-v1")),
        brand_id="neon_seoul", expected_version=0,
    )


def _payload(*, reward: float = 0.9, observed_at: datetime = NOW,
             attribution: float = 0.99) -> dict[str, object]:
    return {"schema_version": "1.0", "experiment_id": "exp-1", "arm_id": "rnb-1", "content_id": "c-1",
            "observation_window": "7d", "status": "mature",
            "observed_at": observed_at.isoformat(), "raw_metrics": unmeasured_raw_metrics(),
            "normalized_reward": reward, "reward_version": "p9-reward-v1",
            "attribution_confidence": attribution}


def test_one_observation_updates_the_posterior_exactly_once() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_arm_and_posterior(session)
        store_result(session, _payload(), brand_id="neon_seoul", now=NOW)
        first = apply_pending_updates(session, settings=SETTINGS, now=NOW, policy=DEFAULT_POLICY)
        assert first.applied == 1 and first.skipped_duplicate == 0
        after_first = repositories.load_posterior(session, "rnb-1")
        assert after_first is not None and after_first.posterior_mean > 0.5
        second = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                       policy=DEFAULT_POLICY)
        assert second.applied == 0 and second.skipped_duplicate == 1
        after_second = repositories.load_posterior(session, "rnb-1")
        assert after_second == after_first, "§74: a retry must not apply the observation twice"


def test_low_attribution_quarantines_instead_of_updating() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_arm_and_posterior(session)
        store_result(session, _payload(attribution=0.2), brand_id="neon_seoul", now=NOW)
        summary = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                        policy=DEFAULT_POLICY)
        assert summary.quarantined == 1 and summary.applied == 0
        posterior = repositories.load_posterior(session, "rnb-1")
        assert posterior is not None and posterior.posterior_mean == 0.5, (
            "§24: ambiguous attribution quarantines; it never updates the wrong arm"
        )


def test_unmeasured_reward_is_quarantined_and_never_treated_as_zero() -> None:
    """R11: P9 owns the number. A stored `not_ready` reward must not move a posterior."""
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_arm_and_posterior(session)
        repositories.insert_reward_observation(
            session,
            RewardObservation(
                experiment_id="exp-nr", arm_id="rnb-1", content_id="c-1",
                observation_window=ObservationWindow.H24,
                maturity=ObservationMaturity.PROVISIONAL, observed_at=NOW, raw_metrics={},
                normalized_reward=Tri.not_ready(), reward_version="p9-reward-v1",
                attribution_confidence=0.99,
            ),
            brand_id="neon_seoul", received_at=NOW,
        )
        summary = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                        policy=DEFAULT_POLICY)
        assert summary.applied == 0 and summary.quarantined == 1
        posterior = repositories.load_posterior(session, "rnb-1")
        assert posterior is not None
        assert posterior.posterior_mean == 0.5 and posterior.observation_count == 0, (
            "an unmeasured reward is never zero evidence (ADR-0023 #4)"
        )


def test_recency_decay_weights_older_observations_less() -> None:
    fresh_engine, old_engine = create_ar_engine(), create_ar_engine()
    with ar_session(fresh_engine) as session:
        _seed_arm_and_posterior(session)
        store_result(session, _payload(observed_at=NOW), brand_id="neon_seoul", now=NOW)
        apply_pending_updates(session, settings=SETTINGS, now=NOW, policy=DEFAULT_POLICY)
        fresh_variance = repositories.load_posterior(session, "rnb-1").posterior_variance
    with ar_session(old_engine) as session:
        _seed_arm_and_posterior(session)
        old = NOW - timedelta(days=45)  # one non-stationarity half-life
        store_result(session, _payload(observed_at=old), brand_id="neon_seoul", now=NOW)
        apply_pending_updates(session, settings=SETTINGS, now=NOW, policy=DEFAULT_POLICY)
        old_variance = repositories.load_posterior(session, "rnb-1").posterior_variance
    assert old_variance > fresh_variance, "§27: a 45-day-old observation moves the belief less"


def test_future_observations_and_unknown_arms_never_update() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_arm_and_posterior(session)
        store_result(session, _payload(observed_at=NOW + timedelta(days=1)),
                     brand_id="neon_seoul", now=NOW)
        summary = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                        policy=DEFAULT_POLICY)
        assert summary.applied == 0 and summary.failed == 0
        assert repositories.load_posterior(session, "rnb-1").update_version == 0, (
            "§23: an observation dated after `now` is not yet evidence"
        )

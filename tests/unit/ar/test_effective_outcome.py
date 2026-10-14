"""ADR-0031 on P2: one effective outcome per (experiment, content), draw-time decay,
and a policy that persists whole."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from plm_contracts import ObservationWindow

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.policy import DEFAULT_POLICY, PolicyConfig
from services.autonomous_ar.domain.posterior import PriorParameters
from services.autonomous_ar.ingestion.result_adapter import store_result
from services.autonomous_ar.modeling.evidence import posterior_as_of
from services.autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from services.autonomous_ar.modeling.update import apply_pending_updates
from services.autonomous_ar.storage import repositories
from services.autonomous_ar.storage.models import ar_allocation_policy, ar_posterior_update_log
from tests.support.ar_db import ar_session, create_ar_engine
from tests.support.seams import unmeasured_raw_metrics

PUBLISHED = datetime(2026, 9, 1, tzinfo=UTC)
NOW = datetime(2026, 10, 1, tzinfo=UTC)
SETTINGS = load_ar_settings()
MODEL = NormalNormalRewardModel()
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _seed(session) -> None:  # type: ignore[no-untyped-def]
    repositories.upsert_arm_state(session, brand_id="neon_seoul", arm_id="rnb-1", now=NOW)
    arm = ContentArm(arm_id="rnb-1", dimensions=ArmDimensions(genre="rnb"),
                     p1_score=60.0, p1_confidence=0.6, component_scores=dict(COMPONENTS),
                     observation_count=1, source_count=1, as_of=NOW, score_version="p1-v1",
                     freshness_seconds=0.0)
    repositories.upsert_posterior(
        session,
        MODEL.initialize_prior(arm, PriorParameters(
            mean=0.5, variance=0.05, confidence=0.6, prior_version="p2-prior-v1")),
        brand_id="neon_seoul", expected_version=0,
    )


def _result(window: str, status: str, reward: float, *, days: int,
            content: str = "c-1") -> dict[str, object]:
    return {"schema_version": "1.0", "experiment_id": "exp-1", "arm_id": "rnb-1",
            "content_id": content, "observation_window": window, "status": status,
            "observed_at": (PUBLISHED + timedelta(days=days)).isoformat(),
            "raw_metrics": unmeasured_raw_metrics(), "normalized_reward": reward,
            "reward_version": "engagement-contribution-proxy-v1",
            "attribution_confidence": 0.95}


def _statuses(session) -> dict[str, str]:  # type: ignore[no-untyped-def]
    rows = session.execute(sa.select(ar_posterior_update_log.c.reward_observation_id,
                                     ar_posterior_update_log.c.update_status)).all()
    return {str(row[0]): row[1] for row in rows}


def test_three_windows_of_one_content_give_one_effective_observation() -> None:
    """Gate R2: 24h, 7d and 28d results for one content move the posterior once."""
    with ar_session(create_ar_engine()) as session:
        _seed(session)
        for window, status, reward, days in (("24h", "provisional", 0.9, 1),
                                             ("7d", "mature", 0.7, 7),
                                             ("28d", "provisional", 0.6, 20),
                                             ("28d", "mature", 0.55, 28)):
            store_result(session, _result(window, status, reward, days=days),
                         brand_id="neon_seoul", now=NOW)
        summary = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                        policy=DEFAULT_POLICY)
        assert (summary.applied, summary.off_horizon, summary.provisional) == (1, 2, 1)
        posterior = repositories.load_posterior(session, "rnb-1")
        assert posterior is not None and posterior.observation_count == 1
        assert sorted(_statuses(session).values()) == [
            "applied", "off_horizon", "off_horizon", "provisional",
        ]


def test_a_second_mature_outcome_at_the_horizon_is_a_duplicate() -> None:
    with ar_session(create_ar_engine()) as session:
        _seed(session)
        store_result(session, _result("28d", "mature", 0.55, days=28),
                     brand_id="neon_seoul", now=NOW)
        store_result(session, _result("28d", "closed", 0.95, days=29),
                     brand_id="neon_seoul", now=NOW)
        summary = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                        policy=DEFAULT_POLICY)
        assert summary.applied == 1 and summary.skipped_duplicate == 1
        posterior = repositories.load_posterior(session, "rnb-1")
        assert posterior is not None and posterior.observation_count == 1


def test_two_contents_of_one_experiment_are_two_outcomes() -> None:
    """§73's old key (experiment, window) collapsed these into one stored result."""
    with ar_session(create_ar_engine()) as session:
        _seed(session)
        for content in ("c-1", "c-2"):
            store_result(session, _result("28d", "mature", 0.6, days=28, content=content),
                         brand_id="neon_seoul", now=NOW)
        summary = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                        policy=DEFAULT_POLICY)
        assert summary.applied == 2


def test_decay_is_measured_from_the_draw_not_from_the_update() -> None:
    with ar_session(create_ar_engine()) as session:
        _seed(session)
        store_result(session, _result("28d", "mature", 0.9, days=28),
                     brand_id="neon_seoul", now=NOW)
        apply_pending_updates(session, settings=SETTINGS, now=NOW, policy=DEFAULT_POLICY)
        stored = repositories.load_posterior(session, "rnb-1")
        assert stored is not None
        soon = posterior_as_of(session, stored, model=MODEL, policy=DEFAULT_POLICY, as_of=NOW)
        later = posterior_as_of(session, stored, model=MODEL, policy=DEFAULT_POLICY,
                                as_of=NOW + timedelta(days=90))
        again = posterior_as_of(session, stored, model=MODEL, policy=DEFAULT_POLICY, as_of=NOW)
        assert later.posterior_variance > soon.posterior_variance, "old evidence weighs less"
        assert abs(later.posterior_mean - 0.5) < abs(soon.posterior_mean - 0.5)
        assert again == soon, "the same evidence at the same instant gives the same posterior"


def test_allocation_fractions_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1.0"):
        PolicyConfig(name="short", exploit_fraction=0.5, adjacent_explore_fraction=0.2,
                     radical_explore_fraction=0.1)


def test_policy_round_trips_every_field_and_legacy_rows_are_refused() -> None:
    policy = PolicyConfig(name="custom", version="p2-v9", min_attribution_confidence=0.8,
                          directive_ttl_hours=24.0, outcome_horizon=ObservationWindow.D7)
    with ar_session(create_ar_engine()) as session:
        repositories.insert_policy(session, policy, brand_id=None, created_at=NOW)
        assert repositories.load_policy(session, "p2-v9") == policy
        session.execute(sa.update(ar_allocation_policy).values(config_json=None))
        with pytest.raises(repositories.LegacyPolicyError):
            repositories.load_policy(session, "p2-v9")

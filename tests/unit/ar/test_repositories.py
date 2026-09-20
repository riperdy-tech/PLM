# tests/unit/ar/test_repositories.py — P2.3's storage contracts on SQLite
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from plm_contracts import AllocationType, ObservationWindow, Tri

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.directive import DirectiveBrief, ProductionDirective
from services.autonomous_ar.domain.enums import (
    AllocationCycleStatus,
    DirectiveStatus,
    ObservationMaturity,
    UpdateStatus,
)
from services.autonomous_ar.domain.policy import PolicyConfig
from services.autonomous_ar.domain.posterior import (
    PosteriorState,
    PriorParameters,
    UpdateRecord,
)
from services.autonomous_ar.domain.reward import RewardObservation, tri_from_optional
from services.autonomous_ar.storage import repositories
from services.autonomous_ar.storage.ids import deterministic_id
from services.autonomous_ar.storage.models import AR_TABLES
from tests.support.ar_db import ar_session, create_ar_engine

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
BRAND = "neon_seoul"
ARM = "arm-1"


def test_policy_rows_are_immutable_once_created() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        policy_id = repositories.insert_policy(
            session, PolicyConfig(name="default"), brand_id="neon_seoul", created_at=NOW
        )
        assert repositories.load_policy(session, "p2-v1").exploit_fraction == 0.70
        # same version, different content: refused (§92 — never mutate a used policy)
        with pytest.raises(repositories.PolicyImmutableError, match="p2-v1"):
            repositories.insert_policy(
                session, PolicyConfig(name="default", exploit_fraction=0.5,
                                      adjacent_explore_fraction=0.3,
                                      radical_explore_fraction=0.2),
                brand_id="neon_seoul", created_at=NOW,
            )
        # replaying the exact same row is idempotent
        again = repositories.insert_policy(
            session, PolicyConfig(name="default"), brand_id="neon_seoul", created_at=NOW
        )
        assert again == policy_id


def test_posterior_update_uses_an_optimistic_version_guard() -> None:
    engine = create_ar_engine()
    prior = PriorParameters(mean=0.6, variance=0.05, confidence=0.7, prior_version="p2-prior-v1")
    state = PosteriorState(
        arm_id="arm-1", model_type="normal-normal", model_version="p2-nn-v1",
        prior_mean=prior.mean, prior_variance=prior.variance, posterior_mean=prior.mean,
        posterior_variance=prior.variance, observation_count=0,
        effective_observation_count=0.0, update_version=0, last_update_at=NOW,
    )
    with ar_session(engine) as session:
        repositories.upsert_posterior(session, state, brand_id="neon_seoul", expected_version=0)
        updated = PosteriorState(
            arm_id="arm-1", model_type="normal-normal", model_version="p2-nn-v1",
            prior_mean=prior.mean, prior_variance=prior.variance, posterior_mean=0.65,
            posterior_variance=0.04, observation_count=1, effective_observation_count=1.0,
            update_version=1, last_update_at=NOW,
        )
        repositories.upsert_posterior(session, updated, brand_id="neon_seoul", expected_version=0)
        # a second writer still holding version 0 must fail loudly (§75), not overwrite
        with pytest.raises(repositories.PosteriorVersionConflict, match="arm-1"):
            repositories.upsert_posterior(session, replace(updated, posterior_mean=0.9),
                                          brand_id="neon_seoul", expected_version=0)
        loaded = repositories.load_posterior(session, "arm-1")
        assert loaded is not None and loaded.posterior_mean == 0.65 and loaded.update_version == 1


# --- the replay proof: the same scenario twice, one row per entity ------------------

ARM_OBJ = ContentArm(
    arm_id=ARM,
    dimensions=ArmDimensions(genre="rnb", moods=("dreamy",)),
    p1_score=82.0,
    p1_confidence=0.8,
    component_scores={
        "demand": 70.0, "growth": None, "competitor_velocity": None, "competition": 55.0,
        "audience_quality": None, "cross_platform": None, "production_fit": None,
    },
    observation_count=3,
    source_count=2,
    as_of=NOW,
    score_version="p1-score-v1",
    freshness_seconds=3600.0,
)

POSTERIOR = PosteriorState(
    arm_id=ARM, model_type="normal-normal", model_version="p2-nn-v1",
    prior_mean=0.55, prior_variance=0.05, posterior_mean=0.55, posterior_variance=0.05,
    observation_count=0, effective_observation_count=0.0, update_version=0, last_update_at=NOW,
)

OBSERVATION = RewardObservation(
    experiment_id="exp-1", arm_id=ARM, content_id="content-1",
    observation_window=ObservationWindow.H24, maturity=ObservationMaturity.PROVISIONAL,
    observed_at=NOW, raw_metrics={"views": Tri.known(1000.0)},
    normalized_reward=Tri.known(0.5), reward_version="p9-reward-v1",
    attribution_confidence=0.8,
)


def _directive(*, directive_id: str, decision_id: str, arm_id: str) -> ProductionDirective:
    return ProductionDirective(
        directive_id=directive_id, decision_id=decision_id, arm_id=arm_id,
        allocation_type=AllocationType.EXPLOIT, priority=1, quantity=1,
        brief=DirectiveBrief(genre="rnb", mood=("dreamy",), use_case=("study_focus",)),
        expected_value=0.5, uncertainty=0.1, budget_limit=0.25,
        market_context={"source": "live", "as_of": NOW.isoformat()},
        selection_reason=("STRONG_POSTERIOR",), directive_version="p2-directive-v1",
        policy_version="p2-v1", expires_at=NOW + timedelta(hours=168), exploration_fraction=0.3,
        value_model="early-stage-proxy-v1",
    )


def _run_scenario(engine: sa.Engine) -> tuple[str, ...]:
    """The P2.10-style write path, run twice in the replay test below.

    Every insert path is called unconditionally so the second run exercises it; only
    the two event recorders (`record_selection`, `finish_cycle`) sit behind the cycle
    guard, exactly as P2.10's replay short-circuit leaves them.
    """
    with ar_session(engine) as session:
        policy_id = repositories.insert_policy(
            session, PolicyConfig(name="default"), brand_id=BRAND, created_at=NOW
        )
        repositories.activate_policy(session, "p2-v1")
        repositories.upsert_arm_state(session, brand_id=BRAND, arm_id=ARM, now=NOW)
        snapshot_id = repositories.insert_feature_snapshot(
            session, brand_id=BRAND, arm=ARM_OBJ, captured_at=NOW,
            feature_version="p2-features-v1",
        )
        repositories.upsert_posterior(session, POSTERIOR, brand_id=BRAND, expected_version=0)
        cycle_id = deterministic_id("cycle", BRAND, NOW.isoformat(), "p2-v1")
        first_time = repositories.insert_cycle(
            session, cycle_id=cycle_id, brand_id=BRAND, started_at=NOW,
            status=AllocationCycleStatus.RUNNING, slot_count=2, budget=10.0,
            policy_id=policy_id, p1_snapshot_time=NOW, activation_state="AUTONOMOUS",
            random_seed=7,
        )
        decision_id = repositories.insert_decision(
            session, cycle_id=cycle_id, arm_id=ARM, allocation_type="exploit",
            sampled_reward=0.6, posterior_expected_reward=0.55, selection_priority=0.6,
            rank_before_constraints=1, rank_after_constraints=1,
            constraint_adjustments={"assumed_cost": 1.0}, reason_codes=("STRONG_POSTERIOR",),
            policy_version="p2-v1", model_version="p2-nn-v1",
            feature_version="p2-features-v1", normalization_version="p2-normalization-v1",
            p1_score_version="p1-score-v1", feature_snapshot_id=snapshot_id, created_at=NOW,
        )
        directive = _directive(
            directive_id=deterministic_id("directive", cycle_id, ARM),
            decision_id=decision_id, arm_id=ARM,
        )
        repositories.insert_directive(
            session, directive, brand_id=BRAND, status=DirectiveStatus.QUEUED, created_at=NOW
        )
        if first_time:
            repositories.record_selection(
                session, arm_id=ARM, now=NOW, recent_exposure_score=1.0
            )
        observation_id, _stored = repositories.insert_reward_observation(
            session, OBSERVATION, brand_id=BRAND, received_at=NOW
        )
        repositories.insert_experiment_result(session, OBSERVATION, brand_id=BRAND)
        repositories.insert_posterior_update(
            session,
            UpdateRecord(reward_observation_id=observation_id, arm_id=ARM, version_before=0,
                         version_after=1, status=UpdateStatus.APPLIED, effective_weight=1.0,
                         updated_at=NOW),
            brand_id=BRAND,
        )
        repositories.replace_similarity(
            session, brand_id=BRAND, pairs=[(ARM, "arm-2", 0.9)],
            similarity_method="cosine", feature_version="p2-features-v1", computed_at=NOW,
        )
        repositories.insert_exposure(
            session, brand_id=BRAND, arm_id=ARM, window_start=NOW,
            window_end=NOW + timedelta(days=7), selection_count=1, content_count=1,
            estimated_audience_exposure=None, exposure_score=1.0,
            calculation_version="p2-exposure-v1",
        )
        repositories.replace_baselines(
            session, brand_id=BRAND, arm_ids=[ARM], now=NOW, config_version="p2-baselines-v1"
        )
        repositories.insert_override(
            session, brand_id=BRAND, target_type="arm", target_id=ARM,
            action="pause_arm", reason="operator request", created_by="operator-1",
            created_at=NOW, expires_at=None,
        )
        repositories.set_channel_state(
            session, brand_id=BRAND, activation_state="SHADOW", now=NOW,
            reason="initial activation", updated_by="operator-1",
        )
        if first_time:
            repositories.finish_cycle(
                session, cycle_id=cycle_id, status=AllocationCycleStatus.COMPLETED,
                finished_at=NOW, decision_count=1, selected_count=1, rejected_count=0,
                warning_codes=[],
            )
        session.commit()
        return (policy_id, snapshot_id, cycle_id, decision_id, directive.directive_id,
                observation_id)


def test_replaying_the_same_scenario_is_idempotent() -> None:
    engine = create_ar_engine()
    first = _run_scenario(engine)
    second = _run_scenario(engine)  # P1's defect: this raised UniqueViolation
    assert first == second, "ids are derived from stable parts, so a replay mints nothing"
    with ar_session(engine) as session:
        for table in AR_TABLES:
            count = session.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
            assert count == 1, f"{table.name}: a replayed scenario must not add a second row"
        state = repositories.load_arm_states(session)[0]
        assert state["selection_count"] == 1, "the replayed cycle is not re-selected (§73)"
        cycle = repositories.load_cycle(session, first[2])
        assert cycle is not None and cycle["decision_count"] == 1


# --- missing data stays missing on both sides of storage ----------------------------

def _observation(experiment_id: str, reward: Tri[float],
                 views: Tri[float] | None = None) -> RewardObservation:
    return RewardObservation(
        experiment_id=experiment_id, arm_id=ARM, content_id="content-1",
        observation_window=ObservationWindow.H24, maturity=ObservationMaturity.PROVISIONAL,
        observed_at=NOW, raw_metrics={"views": views if views is not None else reward},
        normalized_reward=reward,
        reward_version="p9-reward-v1", attribution_confidence=0.8,
    )


def test_absent_zero_and_not_ready_stay_distinguishable_in_storage() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.insert_reward_observation(
            session, _observation("exp-unknown", Tri.unknown()), brand_id=BRAND,
            received_at=NOW,
        )
        repositories.insert_reward_observation(
            session, _observation("exp-zero", Tri.known(0.0)), brand_id=BRAND, received_at=NOW,
        )
        repositories.insert_reward_observation(
            session, _observation("exp-not-ready", Tri.not_ready()), brand_id=BRAND,
            received_at=NOW,
        )
        session.commit()
    with ar_session(engine) as session:
        rows = {row["experiment_id"]: row
                for row in repositories.load_pending_observations(session, now=NOW)}
        unknown, zero, not_ready = rows["exp-unknown"], rows["exp-zero"], rows["exp-not-ready"]
        assert unknown["normalized_reward"] is None
        assert unknown["normalized_reward_state"] == "unknown"
        assert zero["normalized_reward"] == 0.0
        assert zero["normalized_reward_state"] == "known"
        assert not_ready["normalized_reward"] is None
        assert not_ready["normalized_reward_state"] == "not_ready"
        # the raw metric kept its tagged JSON form too — three states, three shapes
        assert zero["raw_metrics_json"]["views"] == {"state": "known", "value": 0.0}
        assert unknown["raw_metrics_json"]["views"] == {"state": "unknown", "value": None}
        assert not_ready["raw_metrics_json"]["views"] == {"state": "not_ready", "value": None}
        # the read path hands back a Tri that still knows which state it is
        assert tri_from_optional(zero["normalized_reward"]).is_zero
        assert tri_from_optional(unknown["normalized_reward"]).is_unknown


# --- ADR-0024 #3: SHADOW directives are auditable but not actionable ----------------

def _seed_cycle_with_directive(session, *, key: str, activation_state: str,
                               arm_id: str) -> tuple[str, str]:
    cycle_id = deterministic_id("cycle", BRAND, key)
    assert repositories.insert_cycle(
        session, cycle_id=cycle_id, brand_id=BRAND, started_at=NOW,
        status=AllocationCycleStatus.RUNNING, slot_count=1, budget=5.0,
        policy_id=deterministic_id("policy", BRAND, "p2-v1"), p1_snapshot_time=NOW,
        activation_state=activation_state, random_seed=1,
    )
    decision_id = repositories.insert_decision(
        session, cycle_id=cycle_id, arm_id=arm_id, allocation_type="exploit",
        sampled_reward=0.5, posterior_expected_reward=0.5, selection_priority=0.5,
        rank_before_constraints=1, rank_after_constraints=1, constraint_adjustments={},
        reason_codes=("BASELINE_CONTROL",), policy_version="p2-v1", model_version="p2-nn-v1",
        feature_version="p2-features-v1", normalization_version="p2-normalization-v1",
        p1_score_version=None, feature_snapshot_id=deterministic_id("no_snapshot", cycle_id,
                                                                    arm_id),
        created_at=NOW,
    )
    directive = _directive(
        directive_id=deterministic_id("directive", cycle_id, arm_id),
        decision_id=decision_id, arm_id=arm_id,
    )
    repositories.insert_directive(
        session, directive, brand_id=BRAND, status=DirectiveStatus.QUEUED, created_at=NOW
    )
    return cycle_id, directive.directive_id


def test_queued_directives_only_serve_actionable_activation_states() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_cycle_with_directive(session, key="autonomous", activation_state="AUTONOMOUS",
                                   arm_id="arm-a")
        _, shadow_directive_id = _seed_cycle_with_directive(
            session, key="shadow", activation_state="SHADOW", arm_id="arm-b"
        )
        session.commit()
    with ar_session(engine) as session:
        served = repositories.list_queued_directives(
            session, brand_id=BRAND, activation_states=["AUTONOMOUS"]
        )
        assert [row["arm_id"] for row in served] == ["arm-a"]
        stored = repositories.load_directive(session, shadow_directive_id)
        assert stored is not None and stored["status"] == "queued", (
            "SHADOW directives stay auditable; they are just not actionable"
        )

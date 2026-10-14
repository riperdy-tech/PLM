# tests/integration/ar/test_replay_idempotency.py
"""PHASE-P1's hardest defect, against the real store: a replayed scenario must not raise.

P1 stored deterministic ids and then hit `UniqueViolation` on the natural key when the
same scenario ran twice against one database. P2 derives its ids the same way, so this
module runs a full write leg — one row in every `ar` table — twice through the real
repositories against the compose postgres, and asserts the replay neither raised nor
doubled a row. Ids and instants are fixed, so the second run re-derives the first run's
keys rather than minting new ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from plm_contracts import AllocationType, ObservationWindow, Tri
from plm_platform.db import build_engine, scoped_session
from sqlalchemy.orm import sessionmaker

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.directive import DirectiveBrief, ProductionDirective
from services.autonomous_ar.domain.enums import (
    AllocationCycleStatus,
    DirectiveStatus,
    ObservationMaturity,
    UpdateStatus,
)
from services.autonomous_ar.domain.policy import PolicyConfig
from services.autonomous_ar.domain.posterior import PosteriorState, UpdateRecord
from services.autonomous_ar.domain.reward import RewardObservation
from services.autonomous_ar.storage import repositories
from services.autonomous_ar.storage.ids import deterministic_id
from services.autonomous_ar.storage.models import AR_TABLES

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
BRAND = "p23_replay_brand"
ARM = "p23-replay-arm"
POLICY = PolicyConfig(name="replay", version="p23-replay-v1")

ARM_OBJ = ContentArm(
    arm_id=ARM,
    dimensions=ArmDimensions(genre="rnb", moods=("dreamy",)),
    p1_score=78.0,
    p1_confidence=0.7,
    component_scores={
        "demand": 64.0, "growth": None, "competitor_velocity": None, "competition": 51.0,
        "audience_quality": None, "cross_platform": None, "production_fit": None,
    },
    observation_count=2,
    source_count=2,
    as_of=NOW,
    score_version="p1-score-v1",
    freshness_seconds=1800.0,
)

POSTERIOR = PosteriorState(
    arm_id=ARM, model_type="normal-normal", model_version="p2-nn-v1",
    prior_mean=0.5, prior_variance=0.05, posterior_mean=0.5, posterior_variance=0.05,
    observation_count=0, effective_observation_count=0.0, update_version=0, last_update_at=NOW,
)

OBSERVATION = RewardObservation(
    experiment_id="p23-replay-exp", arm_id=ARM, content_id="p23-replay-content",
    observation_window=ObservationWindow.H24, maturity=ObservationMaturity.PROVISIONAL,
    observed_at=NOW, raw_metrics={"views": Tri.known(2500.0)},
    normalized_reward=Tri.known(0.42), reward_version="p9-reward-v1",
    attribution_confidence=0.9,
)


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    try:
        resolved = build_engine()
        with resolved.connect() as connection:
            connection.execute(sa.text("SELECT 1 FROM ar.ar_allocation_cycle LIMIT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"compose postgres unavailable or unmigrated ({exc.__class__.__name__}); "
                    "run `docker compose up -d --wait postgres` and `plm migrate`")
    yield resolved
    resolved.dispose()


def _write_leg(engine: sa.Engine) -> tuple[str, ...]:
    """One full P2.3 write leg: a row in every `ar` table, all keys derived from NOW."""
    with scoped_session("ar", factory=sessionmaker(bind=engine)) as session:
        policy_id = repositories.insert_policy(
            session, POLICY, brand_id=BRAND, created_at=NOW
        )
        repositories.activate_policy(session, POLICY.version)
        repositories.upsert_arm_state(session, brand_id=BRAND, arm_id=ARM, now=NOW)
        snapshot_id = repositories.insert_feature_snapshot(
            session, brand_id=BRAND, arm=ARM_OBJ, captured_at=NOW,
            feature_version="p2-features-v1",
        )
        repositories.upsert_posterior(session, POSTERIOR, brand_id=BRAND, expected_version=0)
        cycle_id = deterministic_id("cycle", BRAND, NOW.isoformat(), POLICY.version)
        first_time = repositories.insert_cycle(
            session, cycle_id=cycle_id, brand_id=BRAND, started_at=NOW,
            status=AllocationCycleStatus.RUNNING, slot_count=2, budget=10.0,
            policy_id=policy_id, p1_snapshot_time=NOW, activation_state="AUTONOMOUS",
            random_seed=11,
        )
        decision_id = repositories.insert_decision(
            session, cycle_id=cycle_id, arm_id=ARM, allocation_type="exploit",
            sampled_reward=0.55, posterior_expected_reward=0.5, selection_priority=0.55,
            rank_before_constraints=1, rank_after_constraints=1,
            constraint_adjustments={"assumed_cost": 1.0}, reason_codes=("STRONG_POSTERIOR",),
            policy_version=POLICY.version, model_version="p2-nn-v1",
            feature_version="p2-features-v1", normalization_version="p2-normalization-v1",
            p1_score_version="p1-score-v1", feature_snapshot_id=snapshot_id, created_at=NOW,
        )
        directive = ProductionDirective(
            directive_id=deterministic_id("directive", cycle_id, ARM),
            decision_id=decision_id, arm_id=ARM, allocation_type=AllocationType.EXPLOIT,
            priority=2, quantity=2,
            brief=DirectiveBrief(genre="rnb", mood=("dreamy",), use_case=("study_focus",)),
            expected_value=0.45, uncertainty=0.12, budget_limit=0.3,
            market_context={"source": "live", "as_of": NOW.isoformat()},
            selection_reason=("STRONG_POSTERIOR",), directive_version="p2-directive-v1",
            policy_version=POLICY.version, expires_at=NOW + timedelta(hours=168),
            exploration_fraction=0.3, value_model="early-stage-proxy-v1", outcome_horizon="28d",
        )
        repositories.insert_directive(
            session, directive, brand_id=BRAND, status=DirectiveStatus.QUEUED, created_at=NOW
        )
        if first_time:
            repositories.record_selection(session, arm_id=ARM, now=NOW)
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
            session, brand_id=BRAND, pairs=[(ARM, "p23-replay-arm-2", 0.8)],
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


def test_a_replayed_scenario_is_idempotent_against_one_database(engine: sa.Engine) -> None:
    """The same scenario, twice, against one database — the exact shape that broke P1."""
    first = _write_leg(engine)
    second = _write_leg(engine)  # the replay: P1's UniqueViolation must not recur
    assert first == second, "a replay derives the same ids; it mints nothing new"
    with scoped_session("ar", factory=sessionmaker(bind=engine)) as session:
        for table in AR_TABLES:
            # decisions and directives carry arm ids, not brand ids; the rest carry brand
            has_arm = "arm_id" in table.c
            scope = table.c.arm_id if has_arm else table.c.brand_id
            expected = ARM if has_arm else BRAND
            count = session.execute(
                sa.select(sa.func.count()).select_from(table).where(scope == expected)
            ).scalar_one()
            assert count == 1, f"{table.name}: the replay must not create a second row"
        state = [row for row in repositories.load_arm_states(session)
                 if row["brand_id"] == BRAND]
        assert [row["arm_id"] for row in state] == [ARM]
        assert state[0]["selection_count"] == 1

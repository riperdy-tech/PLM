# tests/integration/ar/test_e2e_acceptance.py
"""§97: P1 snapshot → cycle → directives → rewards → update → the second cycle shifts.

Against the compose postgres (skips cleanly when it is down). The brand is namespaced and
every instant is fixed, so a re-run replays the stored cycles instead of writing second
ones; the assertions hold on that replay too (the P1 `UniqueViolation` lesson): the
update's `applied` count equals the number of results this run actually ingested, which is
2 on a clean database and 0 on a replay.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from plm_platform.db import scoped_session
from sqlalchemy.orm import sessionmaker

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.decisioning.cycle import (
    CycleRequest,
    MarketSnapshot,
    run_allocation_cycle,
)
from services.autonomous_ar.domain.enums import ReasonCode
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.ingestion.result_adapter import store_result
from services.autonomous_ar.modeling.update import apply_pending_updates
from services.autonomous_ar.storage import repositories
from tests.fixtures.ar.scenario import (
    BRAND_ID,
    NOW,
    build_acceptance_environment,
    seed_environment,
)
from tests.support.seams import unmeasured_raw_metrics

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    from plm_platform.db import build_engine

    try:
        resolved = build_engine()
        with resolved.connect() as connection:
            connection.execute(sa.text("SELECT 1 FROM ar.ar_allocation_cycle LIMIT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"compose postgres unavailable or unmigrated ({exc.__class__.__name__}); "
                    "run `docker compose up -d --wait postgres` and `plm migrate`")
    yield resolved
    resolved.dispose()


def test_snapshot_cycle_rewards_update_second_cycle_changes(engine: sa.Engine) -> None:
    environment = build_acceptance_environment()
    settings = load_ar_settings()
    excluded = set(environment.blocked) | set(environment.retired)
    with scoped_session("ar", factory=sessionmaker(bind=engine)) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id=BRAND_ID, created_at=NOW)
        repositories.activate_policy(session, DEFAULT_POLICY.version)
        seed_environment(session, environment, now=NOW)

        first = run_allocation_cycle(
            session,
            CycleRequest(brand_id=BRAND_ID, as_of=NOW, slot_count=10, budget=10.0,
                         policy_version="p2-v1"),
            settings=settings,
            market=MarketSnapshot(arms=environment.arms, as_of=NOW, source="live",
                                  score_version="p1-v1"),
            now=NOW,
        )
        assert len(first.directives) == 10, "the §97 fixture fills a ten-slot cycle"
        assert excluded.isdisjoint({d.arm_id for d in first.directives}), (
            "blocked and retired arms are never selected"
        )
        first_rows = {row["arm_id"]: row for row in
                      repositories.load_decisions(session, first.cycle_id)}
        # The replay path returns no rejection tuples (they are not a stored table); the
        # audit record for a refusal is the decision row, which holds on both paths.
        assert first_rows[environment.stale[0]]["constraint_adjustments_json"] == {
            "ineligible": ReasonCode.MARKET_STALE.value}, (
            "§83: the stale arm is refused as ineligible, on the record"
        )

        # synthetic results for two arms at the policy's declared outcome horizon (only
        # that window's mature outcome moves a posterior, ADR-0031), then §74's update path
        horizon = DEFAULT_POLICY.outcome_horizon
        measured_at = NOW + timedelta(days=28)
        rewarded = [directive.arm_id for directive in first.directives[:2]]
        freshly_stored = 0
        for offset, arm_id in enumerate(rewarded):
            stored = store_result(session, {
                "schema_version": "1.0", "experiment_id": f"p215-cycle-{offset}", "arm_id": arm_id,
                "content_id": f"p215-content-{arm_id}", "observation_window": horizon.value,
                "status": "mature",
                "observed_at": measured_at.isoformat(), "raw_metrics": unmeasured_raw_metrics(),
                "normalized_reward": 0.85, "reward_version": "p9-reward-v1",
                "attribution_confidence": 0.99,
            }, brand_id=BRAND_ID, now=measured_at)
            freshly_stored += int(stored.stored)
        summary = apply_pending_updates(session, settings=settings, now=measured_at,
                                        policy=DEFAULT_POLICY)
        assert summary.applied == freshly_stored, (
            "§73/§74: each newly ingested result applies exactly once (both, on a clean "
            "database); a replayed run applies nothing new and must not fail"
        )
        assert summary.failed == 0 and summary.quarantined == 0

        later = measured_at + timedelta(days=7)
        second = run_allocation_cycle(
            session,
            CycleRequest(brand_id=BRAND_ID, as_of=later, slot_count=10, budget=10.0,
                         policy_version="p2-v1"),
            settings=settings,
            market=MarketSnapshot(arms=environment.arms, as_of=later, source="live",
                                  score_version="p1-v1"),
            now=later,
        )
        assert second.cycle_id != first.cycle_id
        assert excluded.isdisjoint({d.arm_id for d in second.directives})

        second_rows = {row["arm_id"]: row for row in
                       repositories.load_decisions(session, second.cycle_id)}
        for arm_id in rewarded:
            assert second_rows[arm_id]["posterior_expected_reward"] > \
                first_rows[arm_id]["posterior_expected_reward"], (
                    "§97: the policy changes after observed results"
                )
        first_rank = {arm_id: row["rank_before_constraints"]
                      for arm_id, row in first_rows.items()
                      if row["rank_before_constraints"] is not None}
        second_rank = {arm_id: row["rank_before_constraints"]
                       for arm_id, row in second_rows.items()
                       if row["rank_before_constraints"] is not None}
        assert first_rank != second_rank, (
            "§97: the second cycle provably allocates differently, not just re-persisted"
        )
        session.commit()

# tests/integration/ar/test_temporal_jobs.py
"""The workflow runs on `ph2.ar` against the compose stack; skipped when it is down.

The brand is seeded (policy + one stored feature snapshot) so the outcome is
deterministic without a live P1 service: the activity's live read fails (no token in
CI), §102's stored fallback rebuilds the arm from P2's own snapshots — this test is that
degraded path's end-to-end exercise — and the cycle completes in SHADOW.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from plm_platform.db import build_engine, scoped_session
from plm_platform.temporal import TaskQueue, connect_client
from sqlalchemy.orm import sessionmaker
from temporalio.client import Client
from temporalio.worker import Worker

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.jobs import REGISTRATIONS
from services.autonomous_ar.storage import repositories

pytestmark = pytest.mark.integration

BRAND = "ar_temporal_brand"
ARM_ID = "ar-temporal-arm"
COMPONENTS = {name: 55.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


@pytest.fixture(scope="module")
def seeded_stack() -> None:
    """Seed the P2 store the job reads; skip cleanly when compose is down or unmigrated."""
    try:
        engine = build_engine()
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1 FROM ar.ar_allocation_cycle LIMIT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"compose postgres unavailable or unmigrated ({exc.__class__.__name__}); "
                    "run `docker compose up -d --wait postgres` and `plm migrate`")
    now = datetime.now(UTC)
    with scoped_session("ar", factory=sessionmaker(bind=engine)) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id=BRAND, created_at=now)
        repositories.insert_feature_snapshot(
            session, brand_id=BRAND,
            arm=ContentArm(arm_id=ARM_ID,
                           dimensions=ArmDimensions(genre="rnb", moods=("dreamy",),
                                                    use_cases=("late_night_drive",),
                                                    language="english", vocal_mode="vocal"),
                           p1_score=71.0, p1_confidence=0.7, component_scores=COMPONENTS,
                           observation_count=5, source_count=2, as_of=now,
                           score_version="p1-v1", freshness_seconds=0.0),
            captured_at=now, feature_version="p2-features-v1")
        session.commit()
    engine.dispose()


@pytest.mark.integration
def test_allocation_workflow_runs_on_ph2_ar(
        seeded_stack: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLM_SERVICE_TOKEN", raising=False)
    async def scenario() -> dict[str, object]:
        try:
            client: Client = await connect_client()
        except Exception as exc:  # noqa: BLE001 - skip with the named remedy
            pytest.skip(f"temporal unavailable ({exc.__class__.__name__}); run "
                        "`docker compose up -d --wait temporal`")
        registration = REGISTRATIONS[0]
        async with Worker(client, task_queue=TaskQueue.AR.value,
                          workflows=list(registration.workflows),
                          activities=list(registration.activities)):
            handle = await client.start_workflow(
                "AllocationCycleWorkflow",
                {"brand_id": BRAND, "slot_count": 1, "budget": 1.0,
                 "policy_version": "p2-v1"},
                id=f"ar-test-{uuid.uuid4().hex[:8]}", task_queue=TaskQueue.AR.value)
            try:
                return await handle.result()
            except Exception as exc:  # noqa: BLE001 - a named failure still proves wiring
                return {"error": exc.__class__.__name__}

    outcome = asyncio.run(scenario())
    assert outcome.get("cycle_id"), f"the workflow must complete a cycle: {outcome}"
    assert outcome["replay"] is False
    assert outcome["directives"] == 1, "the seeded stored arm fills the single slot"
    assert outcome["degraded"], "no live P1 here: the stored path is the observed one"

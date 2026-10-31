# services/autonomous_ar/jobs/cycle.py
"""Scheduled allocation on `ph2.ar` (§101): a thin Temporal shell around the real engine.

The activity is the boundary where the real world enters: it stamps the cycle instant
once (the schedule's payload carries no clock), reads P1 over the frozen C1 endpoint,
falls back to P2's own last snapshots when P1 is unavailable (§102), runs the engine and
commits. Everything downstream receives that instant — nothing inside the engine reads a
clock (§67), so a re-run of the same request is exactly reproducible.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from plm_platform.temporal import DEFAULT_RETRY_POLICY
from temporalio import activity, workflow

__all__ = ["AllocationCycleWorkflow", "run_cycle_activity"]


@activity.defn
async def run_cycle_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """One allocation cycle: live P1 read, stored fallback, engine, commit.

    `payload` is `CycleRequest`'s fields. `as_of` may be supplied as an aware datetime;
    when it is absent — the schedule's case — the trigger instant is stamped here, once.
    The body blocks (HTTP + SQLAlchemy), so it runs in a thread: the platform worker
    registers no activity executor, and a sync activity would refuse to start.
    """
    return await asyncio.to_thread(_run_cycle, payload)


def _run_cycle(payload: dict[str, Any]) -> dict[str, Any]:
    from plm_platform.config import load_settings
    from plm_platform.db import scoped_session

    from ..config import production_settings
    from ..decisioning.cycle import CycleRequest, MarketSnapshot, run_allocation_cycle
    from ..ingestion.p1_adapter import MarketUnavailable, P1Client, adapt_opportunity

    settings = production_settings(platform=load_settings(env=None))
    platform = settings.platform
    if platform is None:  # pragma: no cover - load_ar_settings above always sets it
        raise RuntimeError("AR settings without platform settings cannot run the job")
    request = CycleRequest(**{**payload, "as_of": payload.get("as_of") or datetime.now(UTC)})
    token = (platform.service_token.get_secret_value()
             if platform.service_token is not None else None)
    client = P1Client(settings.market_base_url, token,
                      timeout_seconds=settings.market_timeout_seconds)
    with scoped_session("ar") as session:
        try:
            page = client.fetch_opportunities(limit=200)
            snapshot = MarketSnapshot(
                arms=tuple(adapt_opportunity(item, now=request.as_of)
                           for item in page.items),
                as_of=page.as_of, source="live", score_version=page.score_version)
        except MarketUnavailable as exc:
            stored = _stored_snapshot(session, brand_id=request.brand_id,
                                      as_of=request.as_of)
            if stored is None:
                raise  # §102: no snapshot and no live data ⇒ explicit failure, no invention
            snapshot = stored
            degraded_reason = str(exc)
        else:
            degraded_reason = ""
        result = run_allocation_cycle(session, request, settings=settings, market=snapshot,
                                      now=request.as_of)
        session.commit()
    return {"cycle_id": result.cycle_id, "status": result.status.value,
            "directives": len(result.directives), "replay": result.replay,
            "degraded": degraded_reason}


def _stored_snapshot(session: Any, *, brand_id: str, as_of: datetime) -> Any:
    """§102's degraded path: rebuild arms from P2's own last snapshots (the dimensions
    ride inside `feature_json`), or return None so the caller fails loudly."""
    from ..decisioning.cycle import MarketSnapshot
    from ..domain.arm import ArmDimensions, ContentArm
    from ..storage import repositories

    rows = repositories.load_all_latest_feature_snapshots(session, brand_id=brand_id)
    if not rows:
        return None
    arms = tuple(
        ContentArm(
            arm_id=row["arm_id"],
            dimensions=ArmDimensions(**row["feature_json"]["dimensions"]),
            p1_score=row["p1_score"], p1_confidence=row["p1_confidence"],
            component_scores=row["feature_json"]["components"],
            observation_count=int(row["feature_json"].get("observation_count", 0)),
            source_count=int(row["feature_json"].get("source_count", 0)),
            as_of=row["captured_at"], score_version=str(row["feature_json"]["score_version"]),
            freshness_seconds=row["freshness_seconds"]
            + max(0.0, (as_of - row["captured_at"]).total_seconds()),
        )
        for row in rows
    )
    return MarketSnapshot(arms=arms, as_of=min(row["captured_at"] for row in rows),
                          source="stored", score_version=arms[0].score_version)


@workflow.defn
class AllocationCycleWorkflow:
    """The scheduled `ar.weekly_allocation` job: one engine run per trigger."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            run_cycle_activity, payload, start_to_close_timeout=timedelta(minutes=30),
            retry_policy=DEFAULT_RETRY_POLICY,
        )

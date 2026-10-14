"""Controlled dry-run smoke test for Phase P9 Analytics & Economics Engine (P9 §26)."""

from __future__ import annotations

from datetime import UTC, datetime

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c7 import ObservationMaturity
from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..domain.enums import CostCategory, ObservationWindow
from ..economics.calculator import compute_unit_economics
from ..evaluation.feedback import evaluate_experiment_outcome, generate_and_post_reward
from ..ingestion.cost import ingest_cost_entry
from ..ingestion.distributor import ingest_distributor_statement
from ..ingestion.youtube import ingest_youtube_metrics

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending analytics service token (PLM_SERVICE_TOKEN)"


def run_smoke_test() -> str | None:
    """Run the P9 dry-run smoke test. Returns None on success, or blocked reason string."""
    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON

    # Test database connectivity
    engine = build_engine(platform)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return f"blocked: database unreachable ({exc})"

    now = datetime.now(UTC)
    ts = int(now.timestamp())
    video_id = f"smoke_vid_{ts}"
    arm_id = f"smoke_arm_{ts}"
    exp_id = f"smoke_exp_{ts}"

    try:
        factory = session_factory(engine)
        with factory() as session:
            # 1. Ingest YouTube metrics
            obs_yt = ingest_youtube_metrics(
                session,
                video_id=video_id,
                channel_id=f"UC_smoke_{ts}",
                date_str="2026-09-26",
                views=15000,
                watch_time_minutes=420.5,
                estimated_revenue_usd=45.20,
            )

            # 2. Ingest distributor statement
            csv_content = "isrc,streams,revenue_usd\nUSPLM2600001,8000,28.50\n"
            _stmt, _tracks = ingest_distributor_statement(
                session,
                provider="mock_dsp",
                period="2026-09",
                raw_content=csv_content,
                source_reference=f"smoke_dist_{ts}.csv",
            )

            # 3. Ingest cost item
            cost = ingest_cost_entry(
                session,
                entity_id=video_id,
                entity_type="video",
                category=CostCategory.COMPUTE,
                amount_usd=12.50,
                period="2026-09",
            )

            # 4. Compute unit economics
            econ = compute_unit_economics(
                video_id,
                "video",
                gross_revenue_usd=obs_yt.estimated_revenue_usd,
                allocated_cost_usd=cost.amount_usd,
                views=obs_yt.views,
            )
            if econ.net_contribution_usd <= 0:
                return f"Unexpected unit economics calculation: net={econ.net_contribution_usd}"

            # 5. Evaluate outcome and post feedback reward
            outcome = evaluate_experiment_outcome(
                session,
                lineage=ExperimentLineage(
                    experiment_id=exp_id,
                    experiment_family_id=f"fam_{ts}",
                    content_arm_id=arm_id,
                    content_genome_id=f"gen_{ts}",
                    production_directive_id=f"dir_{ts}",
                ),
                unit_id=video_id,
                net_contribution_usd=econ.net_contribution_usd,
                views=obs_yt.views,
                watch_time_minutes=obs_yt.watch_time_minutes,
                window=ObservationWindow.D7,
                maturity=ObservationMaturity.MATURE,
                attribution_confidence=1.0,
                quality_status=None,
                now=now,
            )
            reward_payload = generate_and_post_reward(
                session,
                outcome,
                views=obs_yt.views,
                watch_time_minutes=obs_yt.watch_time_minutes,
                average_percentage_viewed=None,
                subscribers_gained=None,
                estimated_revenue=obs_yt.estimated_revenue_usd,
                now=now,
            )
            if reward_payload.normalized_reward <= 0.0 or reward_payload.normalized_reward > 1.0:
                return f"Invalid normalized reward range: {reward_payload.normalized_reward}"

            session.commit()
    except Exception as exc:  # noqa: BLE001
        return f"Analytics smoke execution failed: {exc}"

    return None

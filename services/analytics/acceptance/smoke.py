"""Controlled dry-run smoke test for Phase P9 Analytics & Economics Engine (P9 §26).

The smoke denominates its synthetic facts and its report in one currency
(``SMOKE_CURRENCY``), so it needs no FX rate and never writes one: a made-up rate in the
shared FX table would leak into real reports.
"""

from __future__ import annotations

from datetime import timedelta

from plm_contracts.money import FxTable, Money
from plm_contracts.seams import ExperimentLineage
from plm_platform.clock import SystemClock
from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..domain.enums import CostCategory, ObservationWindow
from ..economics.ledger import entity_economics
from ..evaluation.feedback import evaluate_experiment_outcome, generate_and_post_reward
from ..ingestion.cost import ingest_cost_entry
from ..ingestion.distributor import ingest_distributor_statement
from ..ingestion.youtube import ingest_youtube_metrics
from ..storage.repositories import get_costs_for_entity, get_track_metrics

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending analytics service token (PLM_SERVICE_TOKEN)"
SMOKE_CURRENCY = "USD"


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

    now = SystemClock().now()
    ts = int(now.timestamp())
    video_id = f"smoke_vid_{ts}"
    track_id = f"smoke_trk_{ts}"
    arm_id = f"smoke_arm_{ts}"
    exp_id = f"smoke_exp_{ts}"

    try:
        factory = session_factory(engine)
        with factory() as session:
            # 1. Ingest one YouTube snapshot (a platform estimate)
            obs_yt = ingest_youtube_metrics(
                session,
                video_id=video_id,
                channel_id=f"UC_smoke_{ts}",
                date_str=now.date().isoformat(),
                window=ObservationWindow.D7,
                observed_at=now,
                views=15000,
                watch_time_minutes=420.5,
                estimated_revenue=Money.of("45.20", SMOKE_CURRENCY),
            )

            # 2. Ingest a distributor statement (realized revenue)
            csv_content = f"isrc,track_id,streams,revenue\nUSPLM2600001,{track_id},8000,28.50\n"
            ingest_distributor_statement(
                session,
                provider="mock_dsp",
                period="2026-09",
                currency=SMOKE_CURRENCY,
                raw_content=csv_content,
                source_reference=f"smoke_dist_{ts}.csv",
                now=now,
            )

            # 3. Ingest a cost item
            ingest_cost_entry(
                session,
                entity_id=track_id,
                entity_type="track",
                category=CostCategory.COMPUTE,
                amount=Money.of("12.50", SMOKE_CURRENCY),
                period="2026-09",
                incurred_at=now,
            )
            session.flush()

            # 4. Compute unit economics on realized revenue
            econ = entity_economics(
                entity_id=track_id,
                entity_type="track",
                video_metrics=[],
                track_metrics=get_track_metrics(session, track_id),
                costs=get_costs_for_entity(session, track_id),
                window=ObservationWindow.LIFETIME,
                reporting_currency=SMOKE_CURRENCY,
                fx=FxTable(),
                as_of=now,
            )
            if econ.net_contribution is None or econ.net_contribution.amount <= 0:
                return f"Unexpected unit economics calculation: net={econ.net_contribution}"

            # 5. Evaluate outcome and post the reward proxy
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
                net_contribution=econ.net_contribution,
                reporting_currency=SMOKE_CURRENCY,
                fx=FxTable(),
                views=15000,
                watch_time_minutes=420.5,
                window=ObservationWindow.D7,
                published_at=now - timedelta(days=7),
                attribution_confidence=1.0,
                quality_status=None,
                observed_at=now,
            )
            reward_payload = generate_and_post_reward(
                session,
                outcome,
                views=15000,
                watch_time_minutes=420.5,
                average_percentage_viewed=None,
                subscribers_gained=None,
                estimated_revenue=obs_yt.estimated_revenue,
                now=now,
            )
            if reward_payload.normalized_reward <= 0.0 or reward_payload.normalized_reward > 1.0:
                return f"Invalid normalized reward range: {reward_payload.normalized_reward}"

            session.commit()
    except Exception as exc:  # noqa: BLE001
        return f"Analytics smoke execution failed: {exc}"

    return None

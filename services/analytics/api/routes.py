"""FastAPI router for analytics and economics engine (P9 §88, Ruling R10, R11)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape

from ..domain.enums import CostCategory, ObservationWindow
from ..domain.models import NormalizedRewardPayload, P10FeatureView
from ..economics.calculator import compute_unit_economics
from ..evaluation.feedback import evaluate_experiment_outcome, generate_and_post_reward
from ..ingestion.cost import ingest_cost_entry
from ..ingestion.distributor import ingest_distributor_statement
from ..ingestion.youtube import ingest_youtube_metrics
from ..projection.p10_features import build_p10_feature_view
from ..storage.repositories import (
    get_costs_for_entity,
    get_experiment_outcomes,
    get_track_metrics,
    get_video_metrics,
)
from .schemas import (
    CostIngestRequest,
    DistributorIngestRequest,
    FeedbackRequest,
    HealthResponse,
    YouTubeIngestRequest,
)


def build_analytics_router(
    session_factory: Any,
    auth_dependency: Any,
) -> APIRouter:
    """Build the analytics API router configured with auth and dependencies."""
    router = APIRouter(prefix="/analytics", tags=["analytics"])

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            service="analytics",
            timestamp=datetime.now(UTC).isoformat(),
        )

    @router.post(
        "/ingest/youtube",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(auth_dependency)],
    )
    def ingest_youtube(req: YouTubeIngestRequest) -> dict[str, Any]:
        with session_factory() as session:
            obs = ingest_youtube_metrics(
                session,
                video_id=req.video_id,
                channel_id=req.channel_id,
                date_str=req.date_str,
                views=req.views,
                watch_time_minutes=req.watch_time_minutes,
                average_view_duration_seconds=req.average_view_duration_seconds,
                average_percentage_viewed=req.average_percentage_viewed,
                subscribers_gained=req.subscribers_gained,
                subscribers_lost=req.subscribers_lost,
                estimated_revenue_usd=req.estimated_revenue_usd,
                window=ObservationWindow(req.window),
            )
            session.commit()
            return obs.model_dump()

    @router.post(
        "/ingest/distributor",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(auth_dependency)],
    )
    def ingest_distributor(req: DistributorIngestRequest) -> dict[str, Any]:
        with session_factory() as session:
            stmt, tracks = ingest_distributor_statement(
                session,
                provider=req.provider,
                period=req.period,
                raw_content=req.raw_content,
                source_reference=req.source_reference,
                rows=req.rows,
            )
            session.commit()
            return {
                "statement": stmt.model_dump(),
                "tracks_count": len(tracks),
            }

    @router.post(
        "/ingest/cost",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(auth_dependency)],
    )
    def ingest_cost(req: CostIngestRequest) -> dict[str, Any]:
        with session_factory() as session:
            cost = ingest_cost_entry(
                session,
                entity_id=req.entity_id,
                entity_type=req.entity_type,
                category=CostCategory(req.category),
                amount_usd=req.amount_usd,
                period=req.period,
                description=req.description,
            )
            session.commit()
            return cost.model_dump()

    @router.get(
        "/metrics/videos/{video_id}",
        dependencies=[Depends(auth_dependency)],
    )
    def get_video_metrics_endpoint(video_id: str) -> list[dict[str, Any]]:
        with session_factory() as session:
            metrics = get_video_metrics(session, video_id)
            return [m.model_dump() for m in metrics]

    @router.get(
        "/metrics/tracks/{track_id}",
        dependencies=[Depends(auth_dependency)],
    )
    def get_track_metrics_endpoint(track_id: str) -> list[dict[str, Any]]:
        with session_factory() as session:
            metrics = get_track_metrics(session, track_id)
            return [m.model_dump() for m in metrics]

    @router.get(
        "/economics/{entity_id}",
        dependencies=[Depends(auth_dependency)],
    )
    def get_entity_economics(entity_id: str, entity_type: str = "video") -> dict[str, Any]:
        with session_factory() as session:
            costs = get_costs_for_entity(session, entity_id)
            total_cost = sum(c.amount_usd for c in costs)

            total_rev = 0.0
            views = 0
            streams = 0

            if entity_type == "video":
                v_metrics = get_video_metrics(session, entity_id)
                if v_metrics:
                    total_rev = sum(m.estimated_revenue_usd for m in v_metrics)
                    views = sum(m.views for m in v_metrics)
            else:
                t_metrics = get_track_metrics(session, entity_id)
                if t_metrics:
                    total_rev = sum(m.reported_revenue_usd for m in t_metrics)
                    streams = sum(m.streams for m in t_metrics)

            econ = compute_unit_economics(
                entity_id,
                entity_type,
                gross_revenue_usd=total_rev,
                allocated_cost_usd=total_cost,
                views=views if views > 0 else None,
                streams=streams if streams > 0 else None,
            )
            return econ.model_dump()

    @router.post(
        "/evaluation/feedback",
        response_model=NormalizedRewardPayload,
        dependencies=[Depends(auth_dependency)],
    )
    def post_feedback(req: FeedbackRequest) -> NormalizedRewardPayload:
        with session_factory() as session:
            if req.views is None or req.watch_time_minutes is None:
                raise HTTPException(
                    status_code=422,
                    detail=ErrorShape(
                        code="missing_required_field",
                        field_paths=["views", "watch_time_minutes"],
                        message="the reward proxy needs measured views and watch time",
                    ).model_dump(),
                )
            outcome = evaluate_experiment_outcome(
                session,
                lineage=req.lineage,
                unit_id=req.unit_id,
                net_contribution_usd=req.net_contribution_usd,
                views=req.views,
                watch_time_minutes=req.watch_time_minutes,
                window=req.window,
                maturity=req.maturity,
                attribution_confidence=req.attribution_confidence,
                quality_status=req.quality_status,
                now=req.observed_at,
            )
            payload = generate_and_post_reward(
                session,
                outcome,
                views=req.views,
                watch_time_minutes=req.watch_time_minutes,
                average_percentage_viewed=req.average_percentage_viewed,
                subscribers_gained=req.subscribers_gained,
                estimated_revenue=req.estimated_revenue,
                now=req.observed_at,
            )
            session.commit()
            return payload

    @router.get(
        "/p10/features",
        response_model=list[P10FeatureView],
        dependencies=[Depends(auth_dependency)],
    )
    def get_p10_features(experiment_id: str) -> list[P10FeatureView]:
        with session_factory() as session:
            outcomes = get_experiment_outcomes(session, experiment_id)
            views: list[P10FeatureView] = []
            for outc in outcomes:
                view = build_p10_feature_view(outc)
                views.append(view)
            return views

    return router

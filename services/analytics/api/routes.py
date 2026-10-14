"""FastAPI router for analytics and economics engine (P9 §88, Ruling R10, R11, ADR-0031)."""

from __future__ import annotations

from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape
from plm_contracts.measurement import MeasurementKindError
from plm_contracts.money import CurrencyMismatchError, FxRateMissingError, Money
from plm_platform.clock import Clock
from plm_platform.outbox import OutboxConflictError

from ..config import AnalyticsSettings
from ..domain.enums import ObservationWindow
from ..domain.models import NormalizedRewardPayload, P10FeatureView
from ..economics.ledger import entity_economics
from ..evaluation.feedback import evaluate_experiment_outcome, record_and_enqueue_reward
from ..ingestion.cost import ingest_cost_entry
from ..ingestion.distributor import StatementRejected, ingest_distributor_statement
from ..ingestion.youtube import ingest_youtube_metrics
from ..projection.p10_features import build_p10_feature_view
from ..storage.repositories import (
    FxRateConflictError,
    get_costs_for_entity,
    get_experiment_outcomes,
    get_track_metrics,
    get_video_metrics,
    insert_fx_rate,
    load_fx_table,
)
from .schemas import (
    CostIngestRequest,
    DistributorIngestRequest,
    FeedbackRequest,
    FxRateIngestRequest,
    HealthResponse,
    YouTubeIngestRequest,
)


def _refuse(status_code: int, code: str, message: str, fields: list[str] | None = None) -> NoReturn:
    raise HTTPException(
        status_code=status_code,
        detail=ErrorShape(code=code, field_paths=fields or [], message=message).model_dump(),
    )


def build_analytics_router(
    session_factory: Any,
    auth_dependency: Any,
    *,
    clock: Clock,
    settings: AnalyticsSettings | None = None,
) -> APIRouter:
    """Build the analytics API router configured with auth and dependencies."""
    router = APIRouter(prefix="/analytics", tags=["analytics"])
    reporting_currency = (settings or AnalyticsSettings()).reporting_currency

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            service="analytics",
            timestamp=clock.now().isoformat(),
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
                window=req.window,
                observed_at=req.observed_at,
                views=req.views,
                watch_time_minutes=req.watch_time_minutes,
                average_view_duration_seconds=req.average_view_duration_seconds,
                average_percentage_viewed=req.average_percentage_viewed,
                subscribers_gained=req.subscribers_gained,
                subscribers_lost=req.subscribers_lost,
                estimated_revenue=req.estimated_revenue_money(),
            )
            session.commit()
            return obs.model_dump(mode="json")

    @router.post(
        "/ingest/distributor",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(auth_dependency)],
    )
    def ingest_distributor(req: DistributorIngestRequest) -> dict[str, Any]:
        with session_factory() as session:
            try:
                stmt, tracks = ingest_distributor_statement(
                    session,
                    provider=req.provider,
                    period=req.period,
                    currency=req.currency,
                    raw_content=req.raw_content,
                    source_reference=req.source_reference,
                    rows=req.rows,
                    revenue_status=req.revenue_status,
                    now=clock.now(),
                )
            except StatementRejected as exc:
                session.rollback()
                raise HTTPException(status_code=422, detail=exc.error.model_dump()) from exc
            session.commit()
            return {
                "statement": stmt.model_dump(mode="json"),
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
                category=req.category,
                amount=Money.of(req.amount, req.currency),
                period=req.period,
                description=req.description,
                incurred_at=req.incurred_at,
            )
            session.commit()
            return cost.model_dump(mode="json")

    @router.post(
        "/fx-rates",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(auth_dependency)],
    )
    def ingest_fx_rates(req: FxRateIngestRequest) -> dict[str, Any]:
        """Record conversion rates (ADR-0031). Replays are no-ops; a changed value is 409."""
        with session_factory() as session:
            recorded: list[dict[str, str]] = []
            try:
                for item in req.rates:
                    rate = item.to_rate()
                    row_id = insert_fx_rate(session, rate, recorded_at=clock.now())
                    recorded.append({"id": row_id, "rate_id": rate.rate_id})
            except FxRateConflictError as exc:
                session.rollback()
                _refuse(409, "fx_rate_conflict", str(exc), ["rates"])
            session.commit()
            return {"recorded": recorded}

    @router.get(
        "/metrics/videos/{video_id}",
        dependencies=[Depends(auth_dependency)],
    )
    def get_video_metrics_endpoint(video_id: str) -> list[dict[str, Any]]:
        with session_factory() as session:
            metrics = get_video_metrics(session, video_id)
            return [m.model_dump(mode="json") for m in metrics]

    @router.get(
        "/metrics/tracks/{track_id}",
        dependencies=[Depends(auth_dependency)],
    )
    def get_track_metrics_endpoint(track_id: str) -> list[dict[str, Any]]:
        with session_factory() as session:
            metrics = get_track_metrics(session, track_id)
            return [m.model_dump(mode="json") for m in metrics]

    @router.get(
        "/economics/{entity_id}",
        dependencies=[Depends(auth_dependency)],
    )
    def get_entity_economics(
        entity_id: str,
        entity_type: Literal["video", "track"] = "video",
        window: ObservationWindow = ObservationWindow.LIFETIME,
    ) -> dict[str, Any]:
        """Unit economics in the reporting currency (ADR-0031).

        YouTube views and estimated revenue are the latest snapshot of ``window``
        (default ``lifetime``), never a sum across windows. Statement revenue and costs
        are summed per period. Estimated revenue is reported apart from realized
        revenue and never enters the net contribution.
        """
        with session_factory() as session:
            video = get_video_metrics(session, entity_id) if entity_type == "video" else []
            tracks = get_track_metrics(session, entity_id) if entity_type == "track" else []
            costs = get_costs_for_entity(session, entity_id)
            fx = load_fx_table(session)
            try:
                econ = entity_economics(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    video_metrics=video,
                    track_metrics=tracks,
                    costs=costs,
                    window=window,
                    reporting_currency=reporting_currency,
                    fx=fx,
                    as_of=clock.now(),
                )
            except FxRateMissingError as exc:
                _refuse(409, "fx_rate_missing", str(exc))
            except MeasurementKindError as exc:
                _refuse(409, "measurement_overlap", str(exc))
            return econ.model_dump(mode="json")

    @router.post(
        "/evaluation/feedback",
        response_model=NormalizedRewardPayload,
        dependencies=[Depends(auth_dependency)],
    )
    def post_feedback(req: FeedbackRequest) -> NormalizedRewardPayload:
        if req.views is None or req.watch_time_minutes is None:
            _refuse(
                422,
                "missing_required_field",
                "the reward proxy needs measured views and watch time",
                ["views", "watch_time_minutes"],
            )
        if req.observed_at < req.published_at:
            _refuse(
                422,
                "observation_precedes_publication",
                "an observation cannot predate the publication it measures",
                ["observed_at", "published_at"],
            )
        with session_factory() as session:
            fx = load_fx_table(session)
            try:
                net = fx.convert(
                    req.net_contribution.to_money(), reporting_currency, as_of=req.observed_at
                ).converted
                estimated = (
                    fx.convert(
                        req.estimated_revenue.to_money(), reporting_currency, as_of=req.observed_at
                    ).converted
                    if req.estimated_revenue is not None
                    else None
                )
                outcome = evaluate_experiment_outcome(
                    session,
                    lineage=req.lineage,
                    unit_id=req.unit_id,
                    net_contribution=net,
                    reporting_currency=reporting_currency,
                    fx=fx,
                    views=req.views,
                    watch_time_minutes=req.watch_time_minutes,
                    window=req.window,
                    published_at=req.published_at,
                    closed_by_source=req.closed_by_source,
                    attribution_confidence=req.attribution_confidence,
                    quality_status=req.quality_status,
                    observed_at=req.observed_at,
                )
            except FxRateMissingError as exc:
                session.rollback()
                _refuse(409, "fx_rate_missing", str(exc))
            except CurrencyMismatchError as exc:
                session.rollback()
                _refuse(409, "currency_mismatch", str(exc))
            try:
                # Recorded and queued in this transaction; the dispatcher delivers it.
                payload = record_and_enqueue_reward(
                    session,
                    outcome,
                    views=req.views,
                    watch_time_minutes=req.watch_time_minutes,
                    average_percentage_viewed=req.average_percentage_viewed,
                    subscribers_gained=req.subscribers_gained,
                    estimated_revenue=estimated,
                    now=req.observed_at,
                    enqueued_at=clock.now(),
                )
            except OutboxConflictError as exc:
                session.rollback()
                _refuse(409, "reward_fact_conflict", str(exc))
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
                # P9 does not yet store secondary metrics or quality per outcome; they are
                # served as null (not measured), never as zeros (ADR-0025).
                view = build_p10_feature_view(
                    outc, views=None, watch_time_hours=None, subscribers_gained=None,
                    spotify_streams=None, data_completeness=None, fidelity=None,
                    contamination=None,
                )
                views.append(view)
            return views

    return router

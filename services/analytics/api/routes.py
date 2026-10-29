"""FastAPI router for analytics and economics engine (P9 §88, Ruling R10, R11, ADR-0031)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal, NoReturn

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape, QualityStatus
from plm_contracts.measurement import MeasurementKindError
from plm_contracts.money import CurrencyMismatchError, FxRateMissingError, FxTable, Money
from plm_contracts.seams import ExperimentLineage, SeamRejection, parse_seam
from plm_contracts.seams.c6 import YouTubePublicationPayload
from plm_platform.clock import Clock
from plm_platform.config import SERVICE_TOKEN_HEADER
from plm_platform.decision_log import DecisionConflictError, DecisionNotFoundError
from plm_platform.outbox import OutboxConflictError
from sqlalchemy.orm import Session

from ..config import AnalyticsSettings
from ..domain.enums import MetricSource, ObservationWindow
from ..domain.models import NormalizedRewardPayload, P10FeatureView
from ..economics.ledger import entity_economics
from ..evaluation.decisions import attach_outcome_to_decision
from ..evaluation.delivery import (
    REWARD_TOPIC,
    dispatch_rewards,
    reward_fact_key,
    reward_outbox,
)
from ..evaluation.feedback import evaluate_experiment_outcome, record_and_enqueue_reward
from ..ingestion.cost import ingest_cost_entry
from ..ingestion.distributor import StatementRejected, ingest_distributor_statement
from ..ingestion.releases import (
    ReleaseRecordRejected,
    ingest_distribution_record,
    lineage_for_isrc,
)
from ..ingestion.youtube import ingest_youtube_metrics
from ..ingestion.youtube_client import YouTubeAnalyticsError
from ..ingestion.youtube_reach import ReachReportError
from ..ingestion.youtube_reporting import (
    AuthenticatedReachDownloader,
    ReachReportVersion,
    ReportingAccessDenied,
    ReportingDownloadError,
)
from ..projection.candidate_performance import (
    REPORTING_PACIFIC,
    CandidateAttributionError,
    build_candidate_performance,
    build_reporting_readiness,
    preview_authenticated_reach,
    preview_operator_reach,
)
from ..projection.p10_features import build_p10_feature_view
from ..storage.repositories import (
    FxRateConflictError,
    get_costs_for_entity,
    get_experiment_outcomes,
    get_track_metrics,
    get_track_metrics_by_isrc,
    get_video_metrics,
    insert_fx_rate,
    load_fx_table,
)
from .schemas import (
    AuthenticatedReachPreviewRequest,
    CandidateReachPreviewRequest,
    CostIngestRequest,
    DispatchRequest,
    DistributorIngestRequest,
    FeedbackRequest,
    FxRateIngestRequest,
    HealthResponse,
    MeasureRequest,
    ReachReadinessRequest,
    ReleaseRecordIngestRequest,
    YouTubeIngestRequest,
)

DIRECT_ATTRIBUTION_CONFIDENCE = 1.0
"""A measured unit is attributed through stored identifiers only: the video through its
publication event's lineage, every revenue line through a (release, ISRC) link to the
same lineage. Anything less is refused, so the confidence is 1."""


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
    reward_client: httpx.Client | None = None,
    reward_token: str | None = None,
    youtube_client: httpx.Client | None = None,
    youtube_token: str | None = None,
    reporting_downloader: AuthenticatedReachDownloader | None = None,
) -> APIRouter:
    """Build the analytics API router configured with auth and dependencies.

    ``reward_client`` (base URL = P2) and ``reward_token`` let ``POST
    /analytics/rewards/dispatch`` run the reward outbox; without them it refuses.
    """
    router = APIRouter(prefix="/analytics", tags=["analytics"])
    reporting_currency = (settings or AnalyticsSettings()).reporting_currency

    def evaluate_and_enqueue(
        session: Session, *, lineage: ExperimentLineage, unit_id: str, net: Money,
        fx: FxTable, views: int, watch_time_minutes: float,
        average_percentage_viewed: float | None, subscribers_gained: int | None,
        estimated: Money | None, window: ObservationWindow, published_at: datetime,
        closed_by_source: bool, attribution_confidence: float,
        quality_status: QualityStatus | None, observed_at: datetime,
    ) -> NormalizedRewardPayload:
        """Record the outcome, enqueue its reward and attach it to the decision, in the
        caller's transaction (committed by the caller); every refusal rolls back."""
        try:
            outcome = evaluate_experiment_outcome(
                session, lineage=lineage, unit_id=unit_id, net_contribution=net,
                reporting_currency=reporting_currency, fx=fx, views=views,
                watch_time_minutes=watch_time_minutes, window=window,
                published_at=published_at, closed_by_source=closed_by_source,
                attribution_confidence=attribution_confidence,
                quality_status=quality_status, observed_at=observed_at,
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
                session, outcome, views=views, watch_time_minutes=watch_time_minutes,
                average_percentage_viewed=average_percentage_viewed,
                subscribers_gained=subscribers_gained, estimated_revenue=estimated,
                now=observed_at, enqueued_at=clock.now(),
            )
        except OutboxConflictError as exc:
            session.rollback()
            _refuse(409, "reward_fact_conflict", str(exc))
        try:
            attach_outcome_to_decision(session, outcome, lineage=lineage,
                                       recorded_at=clock.now())
        except DecisionNotFoundError as exc:
            session.rollback()
            _refuse(409, "decision_not_logged", str(exc))
        except DecisionConflictError as exc:
            session.rollback()
            _refuse(409, "decision_result_conflict", str(exc))
        return payload

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
                source=MetricSource(req.source),
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
                    now=req.received_at if req.received_at is not None else clock.now(),
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
        "/ingest/releases",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(auth_dependency)],
    )
    def ingest_release_record(req: ReleaseRecordIngestRequest) -> dict[str, Any]:
        """Link each ISRC of P8's C7 leg-1 record to its lineage (idempotent, ADR-0030)."""
        with session_factory() as session:
            try:
                record = ingest_distribution_record(session, req.record, now=clock.now())
            except ReleaseRecordRejected as exc:
                session.rollback()
                code = 409 if exc.error.code == "lineage_conflict" else 422
                raise HTTPException(status_code=code, detail=exc.error.model_dump()) from exc
            session.commit()
            return {"release_id": record.release_id, "isrcs": list(record.isrcs),
                    "lineage": record.lineage.model_dump(mode="json")}

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

    def _candidate_context(
        attempt_id: str, window: ObservationWindow,
    ) -> dict[str, Any]:
        """Read the authoritative P7 assignment and join one P9 video snapshot.

        This read-only diagnostic neither creates a reward nor claims A/B
        impressions. Without a configured, authenticated P7 source it refuses.
        """
        if youtube_client is None or not youtube_token:
            _refuse(503, "p7_attribution_source_unavailable",
                    "authenticated P7 read source is not configured")
        try:
            upstream = youtube_client.get(
                f"/youtube/attempts/{attempt_id}",
                headers={SERVICE_TOKEN_HEADER: youtube_token},
            )
        except httpx.HTTPError as exc:
            _refuse(502, "p7_attribution_source_failed", str(exc))
        if upstream.status_code == 404:
            _refuse(404, "p7_attempt_not_found", "P7 has no such publish attempt")
        if upstream.status_code != 200:
            _refuse(502, "p7_attribution_source_failed",
                    f"P7 returned HTTP {upstream.status_code}")
        try:
            receipt = upstream.json()
        except ValueError:
            _refuse(502, "p7_attribution_source_invalid", "P7 returned invalid JSON")
        if not isinstance(receipt, dict):
            _refuse(502, "p7_attribution_source_invalid", "P7 receipt must be an object")
        video_id = receipt.get("youtube_video_id")
        if not isinstance(video_id, str) or not video_id:
            _refuse(409, "p7_publication_unverified",
                    "P7 has not established a published video identity")
        with session_factory() as session:
            observations = get_video_metrics(session, video_id)
        try:
            return build_candidate_performance(
                receipt, observations, attempt_id=attempt_id, window=window,
            )
        except CandidateAttributionError as exc:
            _refuse(409, "candidate_attribution_unverified", str(exc))

    @router.get(
        "/candidate-performance/{attempt_id}",
        dependencies=[Depends(auth_dependency)],
    )
    def get_candidate_performance(
        attempt_id: str,
        window: ObservationWindow = ObservationWindow.D28,
    ) -> dict[str, Any]:
        """Read-only published candidate context with no inferred reach/CTR."""
        return _candidate_context(attempt_id, window)

    @router.post(
        "/candidate-performance/{attempt_id}/reach-preview",
        dependencies=[Depends(auth_dependency)],
    )
    def post_candidate_reach_preview(
        attempt_id: str,
        req: CandidateReachPreviewRequest,
    ) -> dict[str, Any]:
        """Check operator-supplied Reporting CSV without persisting or training."""
        context = _candidate_context(attempt_id, req.window)
        try:
            return preview_operator_reach(
                context,
                csv_content=req.reach_csv,
                ctr_unit=req.ctr_unit,
                source_reference=req.source_reference,
                start_date=req.start_date,
                end_date=req.end_date,
                as_of_date=clock.now().astimezone(UTC).date(),
            )
        except (ReachReportError, CandidateAttributionError) as exc:
            _refuse(422, "reach_evidence_invalid", str(exc))

    def _catalog_revision(job_id: str, report_id: str) -> ReachReportVersion:
        """Authenticated catalog lookup; no report download or guessed winner."""
        if reporting_downloader is None:
            _refuse(503, "reporting_source_unavailable",
                    "Google Reporting OAuth source is not configured")
        try:
            revisions = reporting_downloader.list_reach_reports(job_id=job_id)
        except ReportingAccessDenied as exc:
            _refuse(403, "google_reporting_access_denied", str(exc))
        except (ReportingDownloadError, YouTubeAnalyticsError, httpx.HTTPError) as exc:
            _refuse(502, "authenticated_reporting_source_failed", str(exc))
        revision = next((r for r in revisions if r.report_id == report_id), None)
        if revision is None:
            _refuse(404, "reporting_revision_not_found",
                    "requested report ID is not in the authenticated job catalog")
        return revision

    @router.post(
        "/candidate-performance/{attempt_id}/reporting-readiness",
        dependencies=[Depends(auth_dependency)],
    )
    def check_reporting_readiness(
        attempt_id: str, req: ReachReadinessRequest,
    ) -> dict[str, Any]:
        """Read authenticated catalog metadata only; never fetch a report CSV."""
        context = _candidate_context(attempt_id, req.window)
        revision = _catalog_revision(req.job_id, req.report_id)
        try:
            return build_reporting_readiness(
                context, revision=revision,
                start_date=req.start_date, end_date=req.end_date,
                as_of_date=clock.now().astimezone(REPORTING_PACIFIC).date(),
            )
        except CandidateAttributionError as exc:
            _refuse(422, "reporting_readiness_ineligible", str(exc))

    @router.post(
        "/candidate-performance/{attempt_id}/authenticated-reach-preview",
        dependencies=[Depends(auth_dependency)],
    )
    def post_authenticated_reach_preview(
        attempt_id: str,
        req: AuthenticatedReachPreviewRequest,
    ) -> dict[str, Any]:
        """Read an exact Google report revision; never post reach into P2."""
        context = _candidate_context(attempt_id, req.window)
        revision = _catalog_revision(req.job_id, req.report_id)
        if reporting_downloader is None:
            _refuse(503, "reporting_source_unavailable", "Google Reporting source unavailable")
        try:
            report = reporting_downloader.fetch(
                job_id=req.job_id, report_id=req.report_id, ctr_unit=req.ctr_unit,
            )
        except ReportingAccessDenied as exc:
            _refuse(403, "google_reporting_access_denied", str(exc))
        except (ReportingDownloadError, ReachReportError, YouTubeAnalyticsError, httpx.HTTPError) as exc:
            _refuse(502, "authenticated_reporting_source_failed", str(exc))
        try:
            return preview_authenticated_reach(
                context, report=report, revision=revision, ctr_unit=req.ctr_unit,
                start_date=req.start_date, end_date=req.end_date,
                as_of_date=clock.now().astimezone(REPORTING_PACIFIC).date(),
            )
        except CandidateAttributionError as exc:
            _refuse(422, "authenticated_reach_evidence_invalid", str(exc))

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
        views, watch = req.views, req.watch_time_minutes
        assert views is not None and watch is not None  # refused above
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
            except FxRateMissingError as exc:
                _refuse(409, "fx_rate_missing", str(exc))
            payload = evaluate_and_enqueue(
                session, lineage=req.lineage, unit_id=req.unit_id, net=net, fx=fx,
                views=views, watch_time_minutes=watch,
                average_percentage_viewed=req.average_percentage_viewed,
                subscribers_gained=req.subscribers_gained, estimated=estimated,
                window=req.window, published_at=req.published_at,
                closed_by_source=req.closed_by_source,
                attribution_confidence=req.attribution_confidence,
                quality_status=req.quality_status, observed_at=req.observed_at,
            )
            session.commit()
            return payload

    @router.post(
        "/evaluation/measure",
        response_model=NormalizedRewardPayload,
        dependencies=[Depends(auth_dependency)],
    )
    def post_measure(req: MeasureRequest) -> NormalizedRewardPayload:
        """Measure one published unit from stored facts and enqueue its reward (R5).

        Refusals (nothing is recorded): 422 an unreadable publication event or an
        observation before the publication; 409 ``attribution_unlinked`` /
        ``attribution_mismatch`` (an ISRC with no C7 leg-1 link, or linked to another
        lineage), ``measurement_missing`` (no snapshot of the window with views and
        watch time), the economics' blocked reason (``realized_revenue_not_reported``,
        ``cost_not_recorded``, ``unreconciled_records``), ``fx_rate_missing``, and the
        reward/decision conflicts of ``/evaluation/feedback``.
        """
        try:
            publication = parse_seam(YouTubePublicationPayload, req.publication)
        except SeamRejection as exc:
            _refuse(422, exc.error.code, exc.error.message,
                    [f"publication.{p}" for p in exc.field_paths])
        lineage = publication.lineage
        unit_id = publication.youtube_video_id
        observed_at = req.observed_at
        if observed_at < publication.published_at:
            _refuse(422, "observation_precedes_publication",
                    "an observation cannot predate the publication it measures",
                    ["observed_at"])
        with session_factory() as session:
            for isrc in req.isrcs:
                link = lineage_for_isrc(session, isrc)
                if link is None:
                    _refuse(409, "attribution_unlinked",
                            f"ISRC {isrc} has no release link; ingest P8's C7 leg-1 record "
                            "first", ["isrcs"])
                if link[1] != lineage:
                    _refuse(409, "attribution_mismatch",
                            f"ISRC {isrc} was released under another lineage", ["isrcs"])
            video = [m for m in get_video_metrics(session, unit_id)
                     if m.observed_at <= observed_at]
            snapshots = [m for m in video if m.window == req.window and m.views is not None
                         and m.watch_time_minutes is not None]
            if not snapshots:
                _refuse(409, "measurement_missing",
                        f"no {req.window.value} snapshot of {unit_id} with views and watch time "
                        "at or before the observation; unmeasured is not zero")
            snapshot = max(snapshots, key=lambda m: (m.observed_at, m.id))
            lines = [t for isrc in sorted(set(req.isrcs))
                     for t in get_track_metrics_by_isrc(session, isrc)
                     if t.observed_at <= observed_at]
            costs = [c for c in get_costs_for_entity(session, unit_id)
                     if c.incurred_at <= observed_at]
            fx = load_fx_table(session)
            try:
                econ = entity_economics(
                    entity_id=unit_id, entity_type="video", video_metrics=video,
                    track_metrics=lines, costs=costs, window=req.window,
                    reporting_currency=reporting_currency, fx=fx, as_of=observed_at,
                )
            except FxRateMissingError as exc:
                _refuse(409, "fx_rate_missing", str(exc))
            except MeasurementKindError as exc:
                _refuse(409, "measurement_overlap", str(exc))
            if econ.net_contribution is None:
                reason = econ.net_contribution_blocked_reason or "net_contribution_unknown"
                _refuse(409, reason, f"the net contribution of {unit_id} cannot be computed: "
                        f"{reason}")
            assert snapshot.views is not None and snapshot.watch_time_minutes is not None
            payload = evaluate_and_enqueue(
                session, lineage=lineage, unit_id=unit_id, net=econ.net_contribution, fx=fx,
                views=snapshot.views, watch_time_minutes=snapshot.watch_time_minutes,
                average_percentage_viewed=snapshot.average_percentage_viewed,
                subscribers_gained=snapshot.subscribers_gained,
                estimated=econ.estimated_revenue, window=req.window,
                published_at=publication.published_at,
                closed_by_source=req.closed_by_source,
                attribution_confidence=DIRECT_ATTRIBUTION_CONFIDENCE,
                quality_status=req.quality_status, observed_at=observed_at,
            )
            session.commit()
            return payload

    @router.post(
        "/rewards/dispatch",
        dependencies=[Depends(auth_dependency)],
    )
    def post_dispatch(req: DispatchRequest) -> dict[str, Any]:
        """One reward outbox pass to P2, then each named fact's delivery status.

        ``delivered`` is the only status under which a reward counts as posted;
        ``pending`` / ``uncertain`` wait for a later pass; ``failed`` was refused by P2;
        ``null`` means no reward was ever queued for the fact.
        """
        if reward_client is None or not reward_token:
            _refuse(409, "reward_delivery_not_configured",
                    "P9 has no P2 client or service token to deliver rewards")
        report = dispatch_rewards(session_factory, client=reward_client, token=reward_token,
                                  clock=clock, limit=req.limit)
        facts: list[dict[str, Any]] = []
        with session_factory() as session:
            for fact in req.facts:
                key = reward_fact_key(fact.experiment_id, fact.content_id,
                                      fact.observation_window.value, fact.status.value)
                message = reward_outbox.get(session, topic=REWARD_TOPIC, idempotency_key=key)
                facts.append({**fact.model_dump(mode="json"), "fact_key": key,
                              "delivery_status": None if message is None
                              else message.status.value})
        return {"report": asdict(report), "facts": facts}

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

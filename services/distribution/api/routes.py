"""FastAPI router for distribution engine (P8 §71, Ruling R9, R10)."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape
from plm_platform.clearance import ClearanceVerifier
from plm_platform.clock import Clock
from plm_platform.execution import ExternalWriteGuard, ExternalWriteRefusedError
from pydantic import AwareDatetime

from ..client.mock import MockDistributorClient
from ..domain.models import DistributionRelease
from ..engine import DistributionEngine
from ..preflight.validators import DistributionPreflightError
from ..reports.ingest import get_report_reference, ingest_provider_report
from ..statements import StatementSource
from ..storage.repositories import get_release_by_id, get_release_identity, get_tracks_by_release_id
from ..submission import ReleaseMetadata, SubmissionRefusedError, submit_release_from_seams
from .schemas import (
    HealthResponse,
    ReleaseResponse,
    ReleaseSubmitRequest,
    ReportIngestRequest,
    ReportReferenceResponse,
    StatementOut,
    StatementsResponse,
)


def _refusal(exc: ExternalWriteRefusedError) -> dict[str, Any]:
    decision = exc.decision
    return ErrorShape(
        code=f"external_write_{decision.decision.value}",
        field_paths=[],
        message=f"{decision.capability} not performed: {decision.reason}",
    ).model_dump()


def _to_release_response(rel: DistributionRelease) -> ReleaseResponse:
    return ReleaseResponse(
        release_id=rel.release_id,
        artist_id=rel.artist_id,
        release_type=rel.release_type.value,
        release_title=rel.release_title,
        release_version=rel.release_version,
        status=rel.status.value,
        provider=rel.provider,
        provider_release_id=rel.provider_release_id,
        submission_fingerprint=rel.submission_fingerprint,
        deliveries=[
            {
                "store": d.store.value,
                "external_release_id": d.external_release_id,
                "status": d.status.value,
                "first_delivered_at": d.first_delivered_at.isoformat() if d.first_delivered_at else None,
            }
            for d in rel.deliveries
        ],
        created_at=rel.created_at.isoformat(),
        updated_at=rel.updated_at.isoformat(),
    )


def build_distribution_router(
    session_factory: Any,
    auth_dependency: Any,
    engine: DistributionEngine | None = None,
    *,
    clock: Clock,
    clearance: ClearanceVerifier,
    write_guard: ExternalWriteGuard | None = None,
    statements: StatementSource | None = None,
) -> APIRouter:
    """Build the distribution router configured with auth and dependencies.

    ``statements`` is the distributor statement source (only a mock before R6); ``None``
    makes the statement route refuse with ``statement_source_not_configured``.
    """
    router = APIRouter(prefix="/distribution", tags=["distribution"])
    dist_engine = engine or DistributionEngine(
        MockDistributorClient(clock=clock), write_guard=write_guard
    )

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            service="distribution",
            timestamp=clock.now().isoformat(),
        )

    @router.post(
        "/releases",
        response_model=ReleaseResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(auth_dependency)],
    )
    def submit_release(
        req: ReleaseSubmitRequest,
    ) -> ReleaseResponse:
        with session_factory() as session:
            try:
                result = submit_release_from_seams(
                    session,
                    dist_engine,
                    track_manifests=req.track_manifests,
                    media_package=req.media_package,
                    publication=req.publication,
                    clearance=clearance,
                    metadata=ReleaseMetadata(
                        artist_name=req.artist_name,
                        release_date=req.release_date,
                        label_name=req.label_name,
                        copyright_line=req.copyright_line,
                        publisher_line=req.publisher_line,
                        territories=req.territories,
                        release_title=req.release_title,
                        release_version=req.release_version,
                    ),
                    now=clock.now(),
                )
                session.commit()
                response = _to_release_response(result.release)
                if result.payload is not None:
                    response.isrcs = list(result.payload.isrcs)
                    response.distribution_record = result.payload.to_wire()
                response.external_writes = dist_engine.submit_receipts(result)
                return response
            except SubmissionRefusedError as exc:
                session.rollback()
                raise HTTPException(status_code=422, detail=exc.error.model_dump()) from exc
            except DistributionPreflightError as exc:
                raise HTTPException(status_code=422, detail=exc.errors) from exc
            except ExternalWriteRefusedError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=_refusal(exc)) from exc
            except Exception as exc:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get(
        "/releases/{release_id}",
        response_model=ReleaseResponse,
        dependencies=[Depends(auth_dependency)],
    )
    def get_release(
        release_id: str,
    ) -> ReleaseResponse:
        with session_factory() as session:
            rel = get_release_by_id(session, release_id)
            if not rel:
                raise HTTPException(status_code=404, detail=f"Release not found: {release_id}")
            return _to_release_response(rel)

    @router.get(
        "/releases/{release_id}/statements",
        response_model=StatementsResponse,
        dependencies=[Depends(auth_dependency)],
    )
    def get_release_statements(release_id: str, as_of: AwareDatetime) -> StatementsResponse:
        """The release's statements from its submission until ``as_of``, one per month.

        From the configured statement source, keyed by the release's stored arm and ISRCs.
        ``as_of`` is the caller's instant (the pipeline's ``workflow.now()``); one before
        the submission is 422.
        """
        if statements is None:
            raise HTTPException(status_code=409, detail=ErrorShape(
                code="statement_source_not_configured", field_paths=[],
                message="no distributor statement source: the mock needs "
                        "PLM_MOCK_OUTCOMES_FILE and real statement parsing is R6",
            ).model_dump())
        with session_factory() as session:
            release = get_release_by_id(session, release_id)
            if release is None:
                raise HTTPException(status_code=404, detail=ErrorShape(
                    code="release_not_found", field_paths=["release_id"],
                    message=f"Release not found: {release_id}",
                ).model_dump())
            lineage, _ = get_release_identity(session, release_id)
            isrcs = [t.isrc for t in get_tracks_by_release_id(session, release_id)]
        accrual_start = release.created_at
        if accrual_start.tzinfo is None:  # SQLite round-trips timezone=True columns naive
            accrual_start = accrual_start.replace(tzinfo=UTC)
        if as_of < accrual_start:
            raise HTTPException(status_code=422, detail=ErrorShape(
                code="statement_precedes_release", field_paths=["as_of"],
                message="a statement cannot cover time before the release was submitted",
            ).model_dump())
        produced = statements.statements(
            release_id=release_id, isrcs=isrcs, arm_id=lineage.content_arm_id,
            accrual_start=accrual_start, as_of=as_of)
        return StatementsResponse(
            release_id=release_id, provider=statements.provider_name,
            provider_mode=statements.provider_mode.value,
            model_version=statements.model_version,
            accrual_start=accrual_start.isoformat(), as_of=as_of.isoformat(),
            statements=[StatementOut(
                period=s.period, period_start=s.period_start.isoformat(),
                period_end=s.period_end.isoformat(), currency=s.currency, rows=s.rows,
                raw_content=s.raw_content, source_reference=s.source_reference)
                for s in produced],
        )

    @router.post(
        "/releases/{release_id}/reconcile",
        response_model=ReleaseResponse,
        dependencies=[Depends(auth_dependency)],
    )
    def reconcile_release(
        release_id: str,
    ) -> ReleaseResponse:
        with session_factory() as session:
            try:
                result = dist_engine.reconcile_status(session, release_id, now=clock.now())
                session.commit()
                return _to_release_response(result.release)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Exception as exc:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post(
        "/releases/{release_id}/takedown",
        response_model=ReleaseResponse,
        dependencies=[Depends(auth_dependency)],
    )
    def takedown_release(
        release_id: str,
    ) -> ReleaseResponse:
        with session_factory() as session:
            try:
                result = dist_engine.request_takedown(session, release_id, now=clock.now())
                session.commit()
                return _to_release_response(result.release)
            except ExternalWriteRefusedError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=_refusal(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Exception as exc:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post(
        "/reports",
        response_model=ReportReferenceResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(auth_dependency)],
    )
    def ingest_report(
        req: ReportIngestRequest,
    ) -> ReportReferenceResponse:
        with session_factory() as session:
            ref = ingest_provider_report(
                session,
                provider=req.provider,
                provider_period=req.provider_period,
                raw_content=req.raw_content,
                source_reference=req.source_reference,
                raw_payload_uri=req.raw_payload_uri,
                report_type=req.report_type,
                now=clock.now(),
            )
            session.commit()
            return ReportReferenceResponse(
                report_id=ref.report_id,
                provider=ref.provider,
                provider_period=ref.provider_period,
                report_checksum=ref.report_checksum,
                source_reference=ref.source_reference,
                raw_payload_uri=ref.raw_payload_uri,
                report_type=ref.report_type,
                received_at=ref.received_at.isoformat(),
            )

    @router.get(
        "/reports/{report_id}",
        response_model=ReportReferenceResponse,
        dependencies=[Depends(auth_dependency)],
    )
    def get_report(
        report_id: str,
    ) -> ReportReferenceResponse:
        with session_factory() as session:
            ref = get_report_reference(session, report_id)
            if not ref:
                raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")
            return ReportReferenceResponse(
                report_id=ref.report_id,
                provider=ref.provider,
                provider_period=ref.provider_period,
                report_checksum=ref.report_checksum,
                source_reference=ref.source_reference,
                raw_payload_uri=ref.raw_payload_uri,
                report_type=ref.report_type,
                received_at=ref.received_at.isoformat(),
            )

    return router

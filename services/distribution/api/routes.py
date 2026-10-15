"""FastAPI router for distribution engine (P8 §71, Ruling R9, R10)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape
from plm_platform.clearance import ClearanceVerifier
from plm_platform.clock import Clock
from plm_platform.execution import ExternalWriteGuard, ExternalWriteRefusedError

from ..client.mock import MockDistributorClient
from ..domain.models import DistributionRelease
from ..engine import DistributionEngine
from ..preflight.validators import DistributionPreflightError
from ..reports.ingest import get_report_reference, ingest_provider_report
from ..storage.repositories import get_release_by_id
from ..submission import ReleaseMetadata, SubmissionRefusedError, submit_release_from_seams
from .schemas import (
    HealthResponse,
    ReleaseResponse,
    ReleaseSubmitRequest,
    ReportIngestRequest,
    ReportReferenceResponse,
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
) -> APIRouter:
    """Build the distribution router configured with auth and dependencies."""
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
                return _to_release_response(result.release)
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

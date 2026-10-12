"""FastAPI router for distribution engine (P8 §71, Ruling R9, R10)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..artists.manager import get_or_create_artist
from ..client.mock import MockDistributorClient
from ..compiler.release_compiler import ReleaseClearanceError, compile_canonical_release_package
from ..domain.models import DistributionRelease
from ..engine import DistributionEngine
from ..preflight.validators import DistributionPreflightError
from ..reports.ingest import get_report_reference, ingest_provider_report
from ..storage.repositories import get_release_by_id
from .schemas import (
    HealthResponse,
    ReleaseResponse,
    ReleaseSubmitRequest,
    ReportIngestRequest,
    ReportReferenceResponse,
)


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
) -> APIRouter:
    """Build the distribution router configured with auth and dependencies."""
    router = APIRouter(prefix="/distribution", tags=["distribution"])
    dist_engine = engine or DistributionEngine(MockDistributorClient())

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            service="distribution",
            timestamp=datetime.now(UTC).isoformat(),
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
        brand_id = req.brand_id or "brand_neon"

        with session_factory() as session:
            # 1. Resolve or create artist profile
            artist = get_or_create_artist(session, req.artist_name, brand_id=brand_id)

            # 2. Mock or construct p5 clearance and p6 media package
            p5_manifest = {
                "p5_manifest_id": req.p5_manifest_id,
                "disposition": "PASSED",
            }
            media_package = {
                "media_package_id": req.p6_media_package_id,
                "brand_id": brand_id,
                "tracklist": req.tracks or [],
                "thumbnails": [{"uri": f"media/artwork/{req.p6_media_package_id}.jpg"}],
                "audio_master_uri": f"media/audio/{req.p6_media_package_id}.wav",
            }

            try:
                package = compile_canonical_release_package(
                    p5_manifest=p5_manifest,
                    media_package=media_package,
                    artist_id=artist.id,
                    artist_name=artist.canonical_name,
                    brand_id=brand_id,
                    release_title=req.release_title,
                    release_version=req.release_version,
                    release_date=req.release_date,
                    label_name=req.label_name,
                    copyright_line=req.copyright_line,
                    publisher_line=req.publisher_line,
                    territories=req.territories,
                )
            except ReleaseClearanceError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Compilation error: {exc}") from exc

            try:
                result = dist_engine.submit_release(
                    session,
                    package,
                    artist=artist,
                    experiment_id=req.experiment_id,
                    experiment_family_id=req.experiment_family_id,
                    content_arm_id=req.content_arm_id,
                    content_genome_id=req.content_genome_id,
                    production_directive_id=req.production_directive_id,
                    media_package_id=req.media_package_id,
                )
                session.commit()
                return _to_release_response(result.release)
            except DistributionPreflightError as exc:
                raise HTTPException(status_code=422, detail=exc.errors) from exc
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
                result = dist_engine.reconcile_status(session, release_id)
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
                result = dist_engine.request_takedown(session, release_id)
                session.commit()
                return _to_release_response(result.release)
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

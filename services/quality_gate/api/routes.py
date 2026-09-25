"""FastAPI router for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine).

Notice: do NOT import annotations from __future__ here, as FastAPI depends on
runtime types for dependency injection with closures.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape
from sqlalchemy.orm import Session

from ..config import QualityGateSettings
from ..domain.enums import ResolutionCode
from ..gate.engine import run_candidate_evaluation
from ..gate.quarantine import resolve_quarantine
from ..storage.repositories import (
    get_candidate_evaluation,
    get_quarantine_case,
    get_release_manifest_by_candidate,
    insert_candidate_evaluation,
    insert_evaluation_snapshot,
    insert_evidence_item,
    insert_gate_decision,
    insert_lyric_check,
    insert_musical_check,
    insert_policy_check,
    insert_provenance_check,
    insert_quarantine_case,
    insert_release_manifest,
    insert_rights_check,
    insert_risk_finding,
    insert_technical_check,
    list_open_quarantine_cases,
)
from .schemas import (
    EvaluationRequest,
    EvaluationResponse,
    QuarantineCaseResponse,
    QuarantineResolutionRequest,
    ReleaseManifestPayload,
)
from .service_token import require_service_token

__all__ = ["build_router"]


def build_router(
    *,
    session_factory: Callable[[], Session] | None,
    settings: QualityGateSettings,
) -> APIRouter:
    """Build the authenticated router for the quality gate."""
    router = APIRouter(prefix="/qa", tags=["quality_gate"])
    auth_dep = require_service_token(settings.platform)

    def _require_session() -> Iterator[Session]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database session factory not configured for quality gate",
            )
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @router.get("/health", tags=["system"])
    def health_check() -> dict[str, str]:
        return {"status": "ok", "service": "quality_gate"}

    @router.get(
        "/release-manifests/{candidate_id}",
        response_model=ReleaseManifestPayload,
        tags=["manifests"],
        dependencies=[Depends(auth_dep)],
    )
    def get_release_manifest(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> ReleaseManifestPayload:
        manifest = get_release_manifest_by_candidate(session, candidate_id)
        if not manifest:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorShape(
                    code="manifest_not_found",
                    field_paths=["candidate_id"],
                    message=f"No release manifest found for candidate {candidate_id}.",
                ).model_dump(),
            )
        return ReleaseManifestPayload(
            p5_manifest_id=manifest.p5_manifest_id,
            release_manifest_version=manifest.release_manifest_version,
            candidate_id=manifest.candidate_id,
            canonical_artifact_id=manifest.canonical_artifact_id,
            experiment_id=manifest.experiment_id,
            track_spec_id=manifest.track_spec_id,
            disposition=manifest.disposition.value,
            technical_status=manifest.technical_status,
            commercial_use_status=manifest.commercial_use_status,
            provenance_status=manifest.provenance_status,
            ai_disclosure_required=manifest.ai_disclosure_required,
            rights_risk=manifest.rights_risk,
            policy_status=manifest.policy_status,
            evaluation_id=manifest.evaluation_id,
            evaluation_policy_version=manifest.evaluation_policy_version,
            warning_codes=manifest.warning_codes,
            evidence_bundle_id=manifest.evidence_bundle_id,
        )

    @router.post(
        "/evaluations",
        response_model=EvaluationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["evaluations"],
        dependencies=[Depends(auth_dep)],
    )
    def evaluate_candidate_endpoint(
        req: EvaluationRequest,
        session: Annotated[Session, Depends(_require_session)],
    ) -> EvaluationResponse:
        result = run_candidate_evaluation(
            candidate_id=req.candidate_id,
            artifact_id=req.artifact_id,
            audio_path=req.audio_path,
            expected_duration_seconds=req.expected_duration_seconds,
            track_spec_id=req.track_spec_id,
            experiment_id=req.experiment_id,
            content_arm_id=req.content_arm_id,
            vocal_mode=req.vocal_mode,
            lyrics_text=req.lyrics_text,
            prompt_text=req.prompt_text,
            genre_tags=req.genre_tags,
            rights_snapshot=req.rights_snapshot,
            provenance_metadata=req.provenance_metadata,
            evaluation_policy_version=req.evaluation_policy_version,
            trigger=req.trigger,
        )

        # Persist aggregate to database
        insert_candidate_evaluation(session, result.evaluation)
        insert_evaluation_snapshot(session, result.snapshot)

        for tc in result.evaluation.technical_checks:
            insert_technical_check(session, tc)
        for mc in result.evaluation.musical_checks:
            insert_musical_check(session, mc)
        for lc in result.evaluation.lyric_checks:
            insert_lyric_check(session, lc)
        for pc in result.evaluation.provenance_checks:
            insert_provenance_check(session, pc)
        for rc in result.evaluation.rights_checks:
            insert_rights_check(session, rc)
        for pol in result.evaluation.policy_checks:
            insert_policy_check(session, pol)
        for rf in result.evaluation.risk_findings:
            insert_risk_finding(session, rf)
        for ev in result.evaluation.evidence_items:
            insert_evidence_item(session, ev)

        insert_gate_decision(session, result.decision)

        if result.quarantine_case:
            insert_quarantine_case(session, result.quarantine_case)
        if result.release_manifest:
            insert_release_manifest(session, result.release_manifest)

        session.commit()

        return EvaluationResponse(
            evaluation_id=result.evaluation.id,
            candidate_id=result.evaluation.candidate_id,
            status=result.evaluation.status.value,
            disposition=result.evaluation.disposition.value if result.evaluation.disposition else None,
            technical_status=result.evaluation.technical_status,
            quality_status=result.evaluation.quality_status,
            provenance_status=result.evaluation.provenance_status,
            rights_status=result.evaluation.rights_status,
            policy_status=result.evaluation.policy_status,
            composite_quality_score=result.evaluation.composite_quality_score,
            quarantine_case_id=result.quarantine_case.id if result.quarantine_case else None,
            release_manifest_id=result.release_manifest.p5_manifest_id if result.release_manifest else None,
        )

    @router.get(
        "/evaluations/{evaluation_id}",
        response_model=EvaluationResponse,
        tags=["evaluations"],
        dependencies=[Depends(auth_dep)],
    )
    def get_evaluation(
        evaluation_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> EvaluationResponse:
        ev = get_candidate_evaluation(session, evaluation_id)
        if not ev:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorShape(
                    code="evaluation_not_found",
                    field_paths=["evaluation_id"],
                    message=f"Evaluation {evaluation_id} not found.",
                ).model_dump(),
            )
        return EvaluationResponse(
            evaluation_id=ev.id,
            candidate_id=ev.candidate_id,
            status=ev.status.value,
            disposition=ev.disposition.value if ev.disposition else None,
            technical_status=ev.technical_status,
            quality_status=ev.quality_status,
            provenance_status=ev.provenance_status,
            rights_status=ev.rights_status,
            policy_status=ev.policy_status,
            composite_quality_score=ev.composite_quality_score,
        )

    @router.get(
        "/quarantine",
        response_model=list[QuarantineCaseResponse],
        tags=["quarantine"],
        dependencies=[Depends(auth_dep)],
    )
    def list_quarantine_queue(
        session: Annotated[Session, Depends(_require_session)],
    ) -> list[QuarantineCaseResponse]:
        cases = list_open_quarantine_cases(session)
        return [
            QuarantineCaseResponse(
                case_id=c.id,
                candidate_id=c.candidate_id,
                evaluation_id=c.evaluation_id,
                reason_codes=c.reason_codes,
                priority=c.priority,
                status=c.status.value,
                assigned_to=c.assigned_to,
                resolved_at=c.resolved_at.isoformat() if c.resolved_at else None,
                resolution=c.resolution.value if c.resolution else None,
                resolution_notes=c.resolution_notes,
            )
            for c in cases
        ]

    @router.post(
        "/quarantine/{case_id}/resolve",
        response_model=QuarantineCaseResponse,
        tags=["quarantine"],
        dependencies=[Depends(auth_dep)],
    )
    def resolve_quarantine_endpoint(
        case_id: str,
        req: QuarantineResolutionRequest,
        session: Annotated[Session, Depends(_require_session)],
    ) -> QuarantineCaseResponse:
        try:
            res_code = ResolutionCode(req.resolution)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorShape(
                    code="invalid_resolution_code",
                    field_paths=["resolution"],
                    message=f"Invalid resolution code '{req.resolution}'.",
                ).model_dump(),
            )

        existing = get_quarantine_case(session, case_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorShape(
                    code="quarantine_case_not_found",
                    field_paths=["case_id"],
                    message=f"Quarantine case {case_id} not found.",
                ).model_dump(),
            )

        case, _ = resolve_quarantine(
            session=session,
            case_id=case_id,
            resolution=res_code,
            auditor_id=req.auditor_id,
            notes=req.notes,
        )
        session.commit()

        return QuarantineCaseResponse(
            case_id=case.id,
            candidate_id=case.candidate_id,
            evaluation_id=case.evaluation_id,
            reason_codes=case.reason_codes,
            priority=case.priority,
            status=case.status.value,
            assigned_to=case.assigned_to,
            resolved_at=case.resolved_at.isoformat() if case.resolved_at else None,
            resolution=case.resolution.value if case.resolution else None,
            resolution_notes=case.resolution_notes,
        )

    return router

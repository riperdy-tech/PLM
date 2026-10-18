"""FastAPI router for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine).

Notice: do NOT import annotations from __future__ here, as FastAPI depends on
runtime types for dependency injection with closures.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from plm_contracts import ErrorShape
from plm_platform.clock import Clock, SystemClock
from plm_platform.exceptions import DecisionCode, ExceptionCase, ExceptionQueue
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import QualityGateSettings
from ..detectors.config import load_detector_policy
from ..detectors.suite import RightsDetectors
from ..domain.enums import ResolutionCode
from ..domain.ids import evaluation_id as derive_evaluation_id
from ..domain.ids import quarantine_case_id
from ..domain.models import CandidateEvaluation, LegacyManifestError, QuarantineCase
from ..gate.engine import run_candidate_evaluation
from ..gate.persistence import persist_gate_result
from ..gate.quarantine import (
    OverrideNotPossibleError,
    OverrideRefusedError,
    QuarantineAlreadyResolvedError,
    quarantine_holds,
    resolve_quarantine,
)
from ..ingestion.artifact_fetch import (
    ARTIFACT_INTEGRITY_FAILED,
    ARTIFACT_NOT_FOUND,
    ARTIFACT_SHA256_MISMATCH,
    ARTIFACT_STORE_NOT_CONFIGURED,
    ARTIFACT_STORE_UNAVAILABLE,
    ARTIFACT_URI_INVALID,
    ArtifactFetcher,
    ArtifactFetchRefused,
    staged_audio,
)
from ..ingestion.c4_adapter import CandidateEventRejected, consume_c4_event
from ..storage.detector_catalog import SqlInternalCatalog, SqlLyricCorpus, SqlReferenceAudioSet
from ..storage.repositories import (
    get_candidate_evaluation,
    get_quarantine_case,
    get_release_manifest_by_candidate,
    list_open_quarantine_cases,
)
from .schemas import (
    EvaluationRequest,
    EvaluationResponse,
    HoldResponse,
    QuarantineCaseResponse,
    QuarantineResolutionRequest,
    ReleaseManifestPayload,
)
from .service_token import require_service_token

__all__ = ["build_router", "get_clock"]

_IDENTITY_REFUSALS = frozenset(
    {DecisionCode.REVIEWER_ROLE_MISMATCH, DecisionCode.REVIEWER_NOT_AUTHORIZED}
)

_FETCH_REFUSAL_STATUS = {
    ARTIFACT_URI_INVALID: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ARTIFACT_SHA256_MISMATCH: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ARTIFACT_NOT_FOUND: status.HTTP_409_CONFLICT,
    ARTIFACT_INTEGRITY_FAILED: status.HTTP_409_CONFLICT,
    ARTIFACT_STORE_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ARTIFACT_STORE_NOT_CONFIGURED: status.HTTP_503_SERVICE_UNAVAILABLE,
}
"""HTTP status per artifact-read reason code (see :mod:`..ingestion.artifact_fetch`)."""


def get_clock() -> Clock:
    """The wall clock for request handling; tests override this dependency."""
    return SystemClock()


def build_router(
    *,
    session_factory: Callable[[], Session] | None,
    settings: QualityGateSettings,
    artifact_fetcher: ArtifactFetcher | None = None,
) -> APIRouter:
    """Build the authenticated router for the quality gate.

    ``artifact_fetcher`` reads candidate audio by URI and checksum; ``None`` means no
    store is configured and evaluations are refused with 503.
    """
    router = APIRouter(prefix="/qa", tags=["quality_gate"])
    auth_dep = require_service_token(settings.platform)
    queue = ExceptionQueue(reviewers=settings.reviewers)

    def _case_response(session: Session, c: QuarantineCase) -> QuarantineCaseResponse:
        return QuarantineCaseResponse(
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
            holds=[_hold_response(h) for h in quarantine_holds(session, queue, c.id)],
        )

    def _stored_response(session: Session, ev: CandidateEvaluation) -> EvaluationResponse:
        case = get_quarantine_case(session, quarantine_case_id(ev.candidate_id, ev.id))
        manifest = get_release_manifest_by_candidate(session, ev.candidate_id)
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
            quarantine_case_id=case.id if case else None,
            quarantine_reason_codes=list(case.reason_codes) if case else [],
            release_manifest_id=(
                manifest.p5_manifest_id
                if manifest is not None and manifest.evaluation_id == ev.id
                else None
            ),
        )

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
        try:
            wire = manifest.to_wire()
        except LegacyManifestError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorShape(
                    code="legacy_manifest",
                    field_paths=["candidate_id"],
                    message=str(exc),
                ).model_dump(),
            ) from exc
        return ReleaseManifestPayload.model_validate(wire)

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
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> EvaluationResponse:
        try:
            event = consume_c4_event(req.candidate_event)
        except CandidateEventRejected as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ErrorShape(
                    code=exc.error.code,
                    field_paths=[f"candidate_event.{p}" for p in exc.error.field_paths],
                    message=exc.error.message,
                ).model_dump(),
            ) from exc
        # The evaluation id is deterministic per (candidate, policy, trigger): a retried
        # request replays the stored evaluation instead of evaluating twice.
        stored = get_candidate_evaluation(
            session,
            derive_evaluation_id(event.candidate_id, req.evaluation_policy_version, req.trigger),
        )
        if stored is not None:
            if stored.artifact_id != event.artifact_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ErrorShape(
                        code="evaluation_conflict",
                        field_paths=["candidate_event.artifact_id"],
                        message=(
                            f"evaluation {stored.id} judged artifact {stored.artifact_id}, "
                            f"not {event.artifact_id}"
                        ),
                    ).model_dump(),
                )
            return _stored_response(session, stored)
        try:
            if artifact_fetcher is None:
                raise ArtifactFetchRefused(
                    ARTIFACT_STORE_NOT_CONFIGURED,
                    "no artifact store is configured for the quality gate",
                )
            audio = artifact_fetcher.fetch(event.artifact_uri, event.artifact_sha256)
        except ArtifactFetchRefused as exc:
            # Nothing was evaluated, so nothing is recorded: never a pass (R4).
            raise HTTPException(
                status_code=_FETCH_REFUSAL_STATUS.get(
                    exc.error.code, status.HTTP_409_CONFLICT
                ),
                detail=ErrorShape(
                    code=exc.error.code,
                    field_paths=[f"candidate_event.{p}" for p in exc.error.field_paths],
                    message=exc.error.message,
                ).model_dump(),
            ) from exc
        with staged_audio(audio, event.artifact_sha256) as audio_path:
            detector_policy = load_detector_policy()
            result = run_candidate_evaluation(
                event=event,
                audio_path=audio_path,
                clock=clock,
                # The registered catalog, reference recordings and lyric corpus in the qa
                # schema; an empty or unreachable source is UNKNOWN and quarantines.
                detectors=RightsDetectors(
                    internal_catalog=SqlInternalCatalog(session),
                    reference_audio=SqlReferenceAudioSet(
                        session, detector_policy.audio_fingerprint
                    ),
                    lyric_corpus=SqlLyricCorpus(session, detector_policy.lyric_similarity),
                    policy=detector_policy,
                ),
                evaluation_policy_version=req.evaluation_policy_version,
                trigger=req.trigger,
            )

        # Persist aggregate to database. A concurrent request for the same evaluation id
        # (a caller's retry while this one ran) may have committed first: its stored
        # evaluation is the fact and is replayed, never a 500 (R5).
        try:
            persist_gate_result(session, result)
            session.commit()
        except IntegrityError:
            session.rollback()
            winner = get_candidate_evaluation(session, result.evaluation.id)
            if winner is None:
                raise
            if winner.artifact_id != event.artifact_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ErrorShape(
                        code="evaluation_conflict",
                        field_paths=["candidate_event.artifact_id"],
                        message=(f"evaluation {winner.id} judged artifact "
                                 f"{winner.artifact_id}, not {event.artifact_id}"),
                    ).model_dump(),
                ) from None
            return _stored_response(session, winner)

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
            quarantine_reason_codes=(
                list(result.quarantine_case.reason_codes) if result.quarantine_case else []
            ),
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
        return _stored_response(session, ev)

    @router.get(
        "/quarantine",
        response_model=list[QuarantineCaseResponse],
        tags=["quarantine"],
        dependencies=[Depends(auth_dep)],
    )
    def list_quarantine_queue(
        session: Annotated[Session, Depends(_require_session)],
    ) -> list[QuarantineCaseResponse]:
        return [_case_response(session, c) for c in list_open_quarantine_cases(session)]

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
        clock: Annotated[Clock, Depends(get_clock)],
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

        try:
            case, _ = resolve_quarantine(
                session=session,
                case_id=case_id,
                resolution=res_code,
                auditor_id=req.auditor_id,
                notes=req.notes,
                now=clock.now(),
                queue=queue,
                reviewer_role=req.reviewer_role,
                evidence=req.evidence,
            )
        except OverrideRefusedError as exc:
            # Nothing in qa changed; keep the queue's record of the refused attempt.
            session.commit()
            refusal = exc.refusal
            field_paths = [f"evidence.{item}" for item in exc.missing_evidence]
            if refusal.code in _IDENTITY_REFUSALS:
                field_paths = ["auditor_id", "reviewer_role", *field_paths]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorShape(
                    code=refusal.code.value,
                    field_paths=field_paths or ["case_id"],
                    message=str(exc),
                ).model_dump(),
            ) from exc
        except QuarantineAlreadyResolvedError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorShape(
                    code="quarantine_already_resolved", field_paths=["case_id"],
                    message=str(exc),
                ).model_dump(),
            ) from exc
        except OverrideNotPossibleError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorShape(
                    code="override_not_possible", field_paths=["case_id"], message=str(exc)
                ).model_dump(),
            ) from exc
        session.commit()

        return _case_response(session, case)

    return router


def _hold_response(hold: ExceptionCase) -> HoldResponse:
    return HoldResponse(
        hold_id=hold.id,
        reason_code=hold.reason_code,
        hold_class=hold.hold_class.value,
        severity=hold.severity.value,
        override_permitted=hold.override_permitted,
        required_evidence=list(hold.required_evidence),
        reviewer_role=hold.reviewer_role,
        policy_mapped=hold.policy_mapped,
        status=hold.status.value,
        resolution=hold.resolution.value if hold.resolution else None,
    )

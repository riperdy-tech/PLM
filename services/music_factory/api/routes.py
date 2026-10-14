"""API endpoints for Music Generation Factory service (§4.5, §67, §68, R6).

No future annotations here so FastAPI can eagerly evaluate closure dependencies.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from plm_platform.clock import Clock, SystemClock
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..candidates.bank import (
    CandidateNotFoundError,
    InvalidCandidateTransitionError,
    accept_candidate,
    get_candidate_detail,
    mark_candidate_exported_to_p5,
    reject_candidate,
)
from ..config import MusicFactorySettings
from ..generation.engine import GenerationEngine
from ..generation.planner import plan_generation
from ..ingestion.p3_adapter import TrackSpecValidationError, adapt_track_spec
from ..observability.metrics import metrics
from ..providers.registry import list_providers
from ..storage.repositories import (
    get_candidate,
    get_generation_plan,
    get_rights_snapshot_for_attempt,
    list_artifacts_for_candidate,
    list_creative_variants,
    list_generation_attempts,
)
from .events import CandidateExportError, build_candidate_ready_event
from .service_token import require_service_token

__all__ = ["build_router"]


class RejectCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


def build_router(
    *,
    session_factory: Callable[[], Session] | None,
    settings: MusicFactorySettings,
    clock: Clock | None = None,
) -> APIRouter:
    """Build the Music Factory router with fail-closed dependencies."""
    router = APIRouter(prefix="/music", tags=["music"])
    service_clock: Clock = clock or SystemClock()
    auth_dep = require_service_token(settings.platform)

    def _require_session() -> Session:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database session factory not configured for music factory",
            )
        return session_factory()

    # Public health check
    @router.get("/health")
    def health_check() -> dict[str, Any]:
        return {
            "status": "healthy",
            "service": "music_factory",
            "default_provider": settings.default_provider,
        }

    # Protected endpoints
    @router.get("/metrics", dependencies=[Depends(auth_dep)])
    def get_metrics() -> dict[str, Any]:
        return metrics.snapshot()

    @router.get("/providers", dependencies=[Depends(auth_dep)])
    def get_providers() -> list[dict[str, Any]]:
        return [cap.model_dump() for cap in list_providers()]

    @router.post("/plans", dependencies=[Depends(auth_dep)])
    def create_plan_endpoint(
        payload: dict[str, Any],
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        """Create a generation plan from an adapted P3 TrackSpec."""
        try:
            spec = adapt_track_spec(payload)
        except TrackSpecValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.error.model_dump(),
            ) from exc

        plan, variants = plan_generation(spec, created_at=service_clock.now())
        from ..storage.repositories import insert_creative_variant, insert_generation_plan

        try:
            insert_generation_plan(session, plan)
            for v in variants:
                insert_creative_variant(session, v)
            session.commit()
        except Exception:
            session.rollback()
            raise

        return {
            "plan_id": plan.plan_id,
            "status": plan.status.value,
            "candidate_target": plan.candidate_target,
            "max_attempts": plan.max_attempts,
            "variants": [v.model_dump(mode="json") for v in variants],
        }

    @router.post("/plans/{plan_id}/execute", dependencies=[Depends(auth_dep)])
    def execute_plan_endpoint(
        plan_id: str,
        spec_payload: dict[str, Any],
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        """Execute a planned generation run."""
        plan = get_generation_plan(session, plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plan not found: {plan_id}",
            )

        try:
            spec = adapt_track_spec(spec_payload)
        except TrackSpecValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.error.model_dump(),
            ) from exc

        variants = list_creative_variants(session, plan_id)
        engine = GenerationEngine(clock=service_clock, default_provider_id=settings.default_provider)

        result = engine.execute_plan(
            session,
            plan=plan,
            variants=variants,
            spec=spec,
        )

        for attempt in result.attempts:
            metrics.record_attempt(
                provider_id=attempt.provider_id,
                success=attempt.status.value == "succeeded",
                credits=attempt.actual_credits,
            )

        session.commit()

        return {
            "plan_id": result.plan_id,
            "status": result.status.value,
            "candidates": [c.model_dump(mode="json") for c in result.candidates],
            "attempts_count": len(result.attempts),
            "errors": result.errors,
        }

    @router.get("/plans/{plan_id}", dependencies=[Depends(auth_dep)])
    def get_plan_endpoint(
        plan_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        plan = get_generation_plan(session, plan_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plan not found: {plan_id}",
            )
        variants = list_creative_variants(session, plan_id)
        return {
            "plan": plan.model_dump(mode="json"),
            "variants": [v.model_dump(mode="json") for v in variants],
        }

    @router.get("/candidates/{candidate_id}", dependencies=[Depends(auth_dep)])
    def get_candidate_endpoint(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        try:
            detail = get_candidate_detail(session, candidate_id)
        except CandidateNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

        return {
            "candidate": detail.candidate.model_dump(mode="json"),
            "artifacts_count": len(detail.artifacts),
            "has_rights_snapshot": detail.rights_snapshot is not None,
        }

    # Ruling R6 required endpoints
    @router.get("/candidates/{candidate_id}/attempts", dependencies=[Depends(auth_dep)])
    def get_candidate_attempts_endpoint(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> list[dict[str, Any]]:
        cand = get_candidate(session, candidate_id)
        if cand is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Candidate not found: {candidate_id}",
            )
        attempts = list_generation_attempts(session, cand.generation_plan_id)
        return [a.model_dump(mode="json") for a in attempts]

    @router.get("/candidates/{candidate_id}/license", dependencies=[Depends(auth_dep)])
    def get_candidate_license_endpoint(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        cand = get_candidate(session, candidate_id)
        if cand is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Candidate not found: {candidate_id}",
            )
        attempts = list_generation_attempts(session, cand.generation_plan_id)
        if not attempts:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No generation attempts found for candidate",
            )
        snapshot = get_rights_snapshot_for_attempt(session, attempts[0].attempt_id)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rights snapshot not found for candidate attempt",
            )
        return snapshot.model_dump(mode="json")

    @router.get("/candidates/{candidate_id}/artifacts", dependencies=[Depends(auth_dep)])
    def get_candidate_artifacts_endpoint(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> list[dict[str, Any]]:
        cand = get_candidate(session, candidate_id)
        if cand is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Candidate not found: {candidate_id}",
            )
        artifacts = list_artifacts_for_candidate(session, candidate_id)
        return [art.model_dump(mode="json") for art in artifacts]

    @router.get("/candidates/{candidate_id}/provenance", dependencies=[Depends(auth_dep)])
    def get_candidate_provenance_endpoint(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        cand = get_candidate(session, candidate_id)
        if cand is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Candidate not found: {candidate_id}",
            )
        plan = get_generation_plan(session, cand.generation_plan_id)
        variants = list_creative_variants(session, cand.generation_plan_id)
        attempts = list_generation_attempts(session, cand.generation_plan_id)
        artifacts = list_artifacts_for_candidate(session, candidate_id)

        return {
            "candidate_id": cand.candidate_id,
            "track_spec_id": cand.track_spec_id,
            "experiment_id": cand.experiment_id,
            "plan_id": cand.generation_plan_id,
            "policy_version": plan.policy_version if plan else "unknown",
            "provider_id": cand.provider_id,
            "model_id": cand.model_id,
            "variants": [v.model_dump(mode="json") for v in variants],
            "attempts": [a.model_dump(mode="json") for a in attempts],
            "artifacts": [art.model_dump(mode="json") for art in artifacts],
        }

    @router.post("/candidates/{candidate_id}/accept", dependencies=[Depends(auth_dep)])
    def accept_candidate_endpoint(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        try:
            cand = accept_candidate(session, candidate_id, now=service_clock.now())
            session.commit()
            return cand.model_dump(mode="json")
        except CandidateNotFoundError as exc:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except InvalidCandidateTransitionError as exc:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/candidates/{candidate_id}/reject", dependencies=[Depends(auth_dep)])
    def reject_candidate_endpoint(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
        payload: RejectCandidateRequest | None = None,
    ) -> dict[str, Any]:
        reason = payload.reason if payload else None
        try:
            cand = reject_candidate(session, candidate_id, now=service_clock.now(), reason=reason)
            session.commit()
            return cand.model_dump(mode="json")
        except CandidateNotFoundError as exc:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except InvalidCandidateTransitionError as exc:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/candidates/{candidate_id}/export-to-p5", dependencies=[Depends(auth_dep)])
    def export_candidate_endpoint(
        candidate_id: str,
        session: Annotated[Session, Depends(_require_session)],
    ) -> dict[str, Any]:
        """Export a candidate to the P5 gate as a Seam C4 event (ADR-0027).

        The event is built from stored facts first; a candidate with any missing fact is
        refused with 409 and is not marked exported.
        """
        try:
            event = build_candidate_ready_event(session, candidate_id)
        except CandidateExportError as exc:
            session.rollback()
            code = (
                status.HTTP_404_NOT_FOUND
                if exc.error.code == "candidate_not_found"
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(status_code=code, detail=exc.error.model_dump()) from exc
        try:
            cand = mark_candidate_exported_to_p5(session, candidate_id, now=service_clock.now())
        except InvalidCandidateTransitionError as exc:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        session.commit()
        return {"candidate": cand.model_dump(mode="json"), "event": event.to_wire()}

    return router

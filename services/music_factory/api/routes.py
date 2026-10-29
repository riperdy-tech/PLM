"""API endpoints for Music Generation Factory service (§4.5, §67, §68, R6).

No future annotations here so FastAPI can eagerly evaluate closure dependencies.

Mutating routes (plan, execute, export) honour the ``Idempotency-Key`` header
(:mod:`plm_platform.idempotency`): the same key and request replays the stored answer;
the same key with a different request is a 409 ``idempotency_key_reused``. Execute calls
paid providers only through the write guard (R5): a guard refusal answers 409 in the
platform convention, ``external_write_<decision>`` / ``"music.generate not performed:
<reason>"``, with the guard's ``reason`` and the receipts.
"""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from plm_contracts import ErrorShape
from plm_platform.clock import Clock, SystemClock
from plm_platform.execution import ExternalWriteGuard
from plm_platform.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    IdempotencyStore,
    jsonable,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
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
from ..domain.enums import CandidateStatus
from ..generation.engine import (
    GENERATE_CAPABILITY,
    GenerationEngine,
    GenerationPlanSpecMismatch,
)
from ..generation.planner import GenerationPlanCapacityError, plan_generation
from ..ingestion.p3_adapter import TrackSpecValidationError, adapt_track_spec
from ..observability.metrics import metrics
from ..providers.registry import list_providers
from ..storage.artifacts import ArtifactStorageProtocol, ArtifactStorageUnavailable
from ..storage.models import idempotency_records_table
from ..storage.repositories import (
    get_candidate,
    get_generation_plan,
    list_artifacts_for_candidate,
    list_creative_variants,
    list_generation_attempts,
)
from .events import CandidateExportError, build_candidate_ready_event
from .service_token import require_service_token

__all__ = ["build_router"]

IdempotencyKeyHeader = Annotated[str | None, Header(alias=IDEMPOTENCY_KEY_HEADER)]

_IDEMPOTENCY = IdempotencyStore(idempotency_records_table)


class RejectCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


def build_router(
    *,
    session_factory: Callable[[], Session] | None,
    settings: MusicFactorySettings,
    clock: Clock | None = None,
    storage: ArtifactStorageProtocol | None = None,
    write_guard: ExternalWriteGuard | None = None,
) -> APIRouter:
    """Build the Music Factory router with fail-closed dependencies.

    ``storage`` is where generated audio is written; ``None`` means no store is
    configured and generation answers 503 rather than keeping audio anywhere else.
    ``write_guard`` decides every paid provider call; without one, every capability is
    dry-run with no budget, so only a mock provider is ever called.
    """
    router = APIRouter(prefix="/music", tags=["music"])
    service_clock: Clock = clock or SystemClock()
    guard = write_guard or ExternalWriteGuard()
    auth_dep = require_service_token(settings.platform)

    def _require_session() -> Iterator[Session]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database session factory not configured for music factory",
            )
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _idempotent(
        session: Session,
        request: Request,
        key: str | None,
        body: Any,
        operation: Callable[[], tuple[int, Any]],
    ) -> JSONResponse:
        try:
            stored = _IDEMPOTENCY.run(
                session, key, method=request.method, path=request.url.path, body=body,
                now=service_clock.now, operation=operation,
            )
        except IdempotencyKeyReusedError as err:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorShape(code=err.code, field_paths=[IDEMPOTENCY_KEY_HEADER],
                                  message=str(err)).model_dump(),
            ) from err
        except IdempotencyKeyInvalidError as err:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ErrorShape(code=err.code, field_paths=[IDEMPOTENCY_KEY_HEADER],
                                  message=str(err)).model_dump(),
            ) from err
        except Exception:
            session.rollback()
            raise
        return JSONResponse(status_code=stored.status_code, content=stored.body)

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
        request: Request,
        session: Annotated[Session, Depends(_require_session)],
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> JSONResponse:
        """Create a generation plan from an adapted P3 TrackSpec."""

        def operation() -> tuple[int, Any]:
            try:
                spec = adapt_track_spec(payload)
            except TrackSpecValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=exc.error.model_dump(),
                ) from exc

            try:
                plan, variants = plan_generation(spec, created_at=service_clock.now())
            except GenerationPlanCapacityError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=ErrorShape(
                        code="generation_plan_capacity_exceeded",
                        field_paths=["candidate_target"],
                        message=str(exc),
                    ).model_dump(),
                ) from exc
            from ..storage.repositories import insert_creative_variant, insert_generation_plan

            stored = get_generation_plan(session, plan.plan_id)
            if stored is None:
                try:
                    # A concurrent request may win the deterministic plan ID.
                    # A savepoint lets us read its committed plan after UNIQUE
                    # refusal without aborting the enclosing idempotency txn.
                    with session.begin_nested():
                        insert_generation_plan(session, plan)
                        for variant in variants:
                            insert_creative_variant(session, variant)
                except IntegrityError:
                    stored = get_generation_plan(session, plan.plan_id)
                    if stored is None:
                        raise  # An unrelated database constraint failed.

            if stored is not None:
                immutable_fields = (
                    "track_spec_id", "experiment_id", "lineage",
                    "track_spec_wire", "candidate_target", "max_attempts",
                    "budget_limit", "credit_limit", "policy_version",
                )
                persisted_variants = list_creative_variants(session, plan.plan_id)
                by_id = {variant.variant_id: variant for variant in persisted_variants}
                matching_variants = (
                    len(persisted_variants) == len(variants)
                    and all(
                        expected.variant_id in by_id
                        and all(
                            getattr(by_id[expected.variant_id], field)
                            == getattr(expected, field)
                            for field in (
                                "generation_plan_id", "variant_code",
                                "parent_variant_id", "changed_dimensions",
                                "change_reason", "prompt_delta",
                            )
                        )
                        for expected in variants
                    )
                )
                if (
                    any(getattr(stored, field) != getattr(plan, field)
                        for field in immutable_fields)
                    or not matching_variants
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=ErrorShape(
                            code="generation_plan_identity_conflict",
                            field_paths=["track_spec_id", "generation_plan_id"],
                            message="plan ID already exists with a different C3 brief or variants",
                        ).model_dump(),
                    )
                plan = stored
                variants = [by_id[v.variant_id] for v in variants]

            return 200, jsonable({
                "plan_id": plan.plan_id,
                "status": plan.status.value,
                "candidate_target": plan.candidate_target,
                "max_attempts": plan.max_attempts,
                "variants": [v.model_dump(mode="json") for v in variants],
            })

        return _idempotent(session, request, idempotency_key, payload, operation)

    @router.post("/plans/{plan_id}/execute", dependencies=[Depends(auth_dep)])
    def execute_plan_endpoint(
        plan_id: str,
        spec_payload: dict[str, Any],
        request: Request,
        session: Annotated[Session, Depends(_require_session)],
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> JSONResponse:
        """Execute a planned generation run.

        Every provider call is authorized by the write guard (``music.generate``, key =
        attempt id, budget from the ledger). A refusal stops the plan before the call:
        409 ``external_write_<decision>`` with ``detail.reason`` (the guard's code,
        e.g. ``budget_ledger_unavailable``, ``no_authorization``, ``policy_dry_run``,
        ``exhausted``); work done before the stop is kept, and re-executing resumes.
        """
        return _idempotent(session, request, idempotency_key, spec_payload,
                           lambda: _execute(plan_id, spec_payload, session))

    def _execute(plan_id: str, spec_payload: dict[str, Any],
                 session: Session) -> tuple[int, Any]:
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

        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=ErrorShape(
                    code="artifact_store_not_configured",
                    field_paths=[],
                    message="no artifact store is configured for the music factory",
                ).model_dump(),
            )
        variants = list_creative_variants(session, plan_id)
        engine = GenerationEngine(
            clock=service_clock, storage=storage, default_provider_id=settings.default_provider,
            write_guard=guard,
        )

        try:
            result = engine.execute_plan(
                session,
                plan=plan,
                variants=variants,
                spec=spec,
            )
        except GenerationPlanSpecMismatch as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorShape(
                    code="generation_plan_spec_mismatch",
                    field_paths=["generation_plan_id", "track_spec_id"],
                    message=str(exc),
                ).model_dump(),
            ) from exc
        except ArtifactStorageUnavailable as exc:
            # Nothing is recorded for audio that could not be stored (fail closed).
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=ErrorShape(
                    code="artifact_store_unavailable", field_paths=[], message=str(exc)
                ).model_dump(),
            ) from exc

        for attempt in result.attempts:
            metrics.record_attempt(
                provider_id=attempt.provider_id,
                success=attempt.status.value == "succeeded",
                credits=attempt.actual_credits,
                credit_basis=attempt.credit_basis,
            )

        external_writes = [
            {"capability": d.capability, "idempotency_key": d.idempotency_key,
             "decision": d.decision.value, "reason": d.reason,
             "provider_mode": d.provider_mode.value, "policy": d.policy.value}
            for d in result.write_decisions
        ]
        if result.stopped_decision is not None:
            decision = result.stopped_decision
            # Returned, not raised: provider calls made before the stop are kept.
            return status.HTTP_409_CONFLICT, jsonable({"detail": {
                # The platform's external-write refusal convention, shared with P7 and
                # P8: code external_write_<decision>, message "<capability> not
                # performed: <reason>" (the pipeline maps the reason to its hold).
                **ErrorShape(
                    code=f"external_write_{decision.decision.value}",
                    field_paths=[GENERATE_CAPABILITY],
                    message=f"{GENERATE_CAPABILITY} not performed: {decision.reason}",
                ).model_dump(),
                "reason": decision.reason,
                "decision": decision.decision.value,
                "plan_id": plan_id,
                "attempt_id": decision.idempotency_key,
                "candidates": [c.candidate_id for c in result.candidates],
                "external_writes": external_writes,
            }})

        return 200, jsonable({
            "plan_id": result.plan_id,
            "status": result.status.value,
            "candidates": [c.model_dump(mode="json") for c in result.candidates],
            "attempts_count": len(result.attempts),
            "errors": result.errors,
            "external_writes": external_writes,
        })

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
        try:
            detail = get_candidate_detail(session, candidate_id)
        except CandidateNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
            ) from exc
        # Rights belong to the exact producing attempt, not the plan's first
        # attempt (which can belong to another candidate). Legacy or broken
        # links have no verifiable license and must fail closed.
        if detail.attempt is None or detail.rights_snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No verified rights snapshot for candidate's producing attempt",
            )
        return detail.rights_snapshot.model_dump(mode="json")

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
        request: Request,
        session: Annotated[Session, Depends(_require_session)],
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> JSONResponse:
        return _idempotent(session, request, idempotency_key, None,
                           lambda: _export(candidate_id, session))

    def _export(candidate_id: str, session: Session) -> tuple[int, Any]:
        """Export a candidate to the P5 gate as a Seam C4 event (ADR-0027).

        The event is built from stored facts first; a candidate with any missing fact is
        refused with 409 and is not marked exported. Re-exporting a candidate already
        exported returns the same event (it is rebuilt from the same stored facts) and
        changes nothing, so a retried pipeline step reads the event it needs.
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
        current = get_candidate(session, candidate_id)
        if current is not None and current.status == CandidateStatus.EXPORTED_TO_P5:
            return 200, jsonable(
                {"candidate": current.model_dump(mode="json"), "event": event.to_wire()})
        try:
            cand = mark_candidate_exported_to_p5(session, candidate_id, now=service_clock.now())
        except InvalidCandidateTransitionError as exc:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return 200, jsonable({"candidate": cand.model_dump(mode="json"), "event": event.to_wire()})

    return router

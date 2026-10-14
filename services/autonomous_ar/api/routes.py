# services/autonomous_ar/api/routes.py
"""§71's endpoints, literal paths. Auth first on every route; writes commit; no clock.

The serving rule is ADR-0024: only cycles whose persisted `activation_state` is
`AUTONOMOUS` are actionable, so `GET /ar/directives` serves nothing while the deployment
runs in `SHADOW` — the compute, the decisions and the directives stay auditable, nothing
has a production effect. Every served item is a `ProductionDirective`, validated through
the frozen C2 model, so the wire cannot silently grow or lose a field.

Determinism: the cycle's instant is the request's `as_of` (the persisted cycle instant,
§67); a result's receipt instant is the observation's own `observed_at`; the update
worker's `now` defaults to the newest stored observation instant. Nothing here reads a
wall clock or mints an id.

No ``from __future__ import annotations`` here on purpose: FastAPI resolves
``Annotated[..., Depends(...)]`` metadata by evaluating the annotation, and the
dependencies are per-app closures that only the eager form can see (P1.10's rule).
"""

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from plm_contracts import ChannelActivationState, ErrorShape
from plm_contracts.seams.c2 import ProductionDirectivePayload
from sqlalchemy.orm import Session

from ..config import ARSettings
from ..decisioning.cycle import (
    CycleRequest,
    CycleResult,
    MarketSnapshot,
    PolicyNotConfigured,
    run_allocation_cycle,
)
from ..domain.directive import ProductionDirective
from ..domain.posterior import PosteriorState
from ..ingestion.p1_adapter import MarketUnavailable, P1Client, adapt_opportunity
from ..ingestion.result_adapter import (
    ResultValidationError,
    parse_result_payload,
    store_result,
)
from ..modeling.update import apply_pending_updates
from ..storage import repositories
from .schemas import (
    ArmStateOut,
    CycleCreateRequest,
    CycleSummaryOut,
    DecisionOut,
    ResultAccepted,
    UpdateSummaryOut,
)
from .service_token import require_service_token

__all__ = ["build_router"]

SessionFactory = Callable[[], Session]
MarketSource = Callable[[CycleRequest], MarketSnapshot]

_ACTIONABLE_ACTIVATION_STATES: tuple[str, ...] = (ChannelActivationState.AUTONOMOUS.value,)
"""ADR-0024: only AUTONOMOUS cycles are actionable. SHADOW/DRY_RUN stay auditable only."""


def build_router(*, session_factory: SessionFactory | None, settings: ARSettings,
                 market_source: MarketSource | None = None) -> APIRouter:
    """Auth first, always: `_auth` is the first dependency on every route, so a request
    that fails the token check never reaches the database dependency or the engine."""
    router = APIRouter()
    dependency = _dependencies(session_factory)
    auth = _auth(settings)

    @router.post("/ar/cycles", response_model=CycleSummaryOut)
    def create_cycle(payload: CycleCreateRequest, _: Annotated[None, Depends(auth)],
                     session: Annotated[Session, Depends(dependency)]) -> CycleSummaryOut:
        request = CycleRequest(
            brand_id=payload.brand_id, as_of=payload.as_of, slot_count=payload.slot_count,
            budget=payload.budget, policy_version=payload.policy_version,
            activation_state=payload.activation_state, random_seed=payload.random_seed,
            unavailable_capabilities=tuple(payload.unavailable_capabilities),
        )
        snapshot = _resolve_snapshot(request, settings=settings, market_source=market_source)
        try:
            # `now` is the request's own instant: the cycle is deterministic from stored
            # inputs, and a re-post of the same body replays the stored cycle (§67).
            result = run_allocation_cycle(session, request, settings=settings, market=snapshot,
                                          now=payload.as_of)
        except PolicyNotConfigured as exc:
            raise _conflict("policy_not_configured", str(exc)) from exc
        session.commit()
        return _summary(session, result)

    @router.get("/ar/cycles/{cycle_id}", response_model=CycleSummaryOut)
    def get_cycle(cycle_id: str, _: Annotated[None, Depends(auth)],
                  session: Annotated[Session, Depends(dependency)]) -> CycleSummaryOut:
        row = repositories.load_cycle(session, cycle_id)
        if row is None:
            raise _not_found("cycle", cycle_id)
        return _summary_from_row(row, replay=False)

    @router.get("/ar/cycles/{cycle_id}/decisions", response_model=list[DecisionOut])
    def get_decisions(cycle_id: str, _: Annotated[None, Depends(auth)],
                      session: Annotated[Session, Depends(dependency)]) -> list[DecisionOut]:
        if repositories.load_cycle(session, cycle_id) is None:
            raise _not_found("cycle", cycle_id)
        return [_decision(row) for row in repositories.load_decisions(session, cycle_id)]

    @router.get("/ar/directives", response_model=list[ProductionDirectivePayload])
    def list_directives(_: Annotated[None, Depends(auth)],
                        session: Annotated[Session, Depends(dependency)],
                        brand_id: str = Query(min_length=1)) -> list[dict[str, Any]]:
        """Queued directives as the versioned C2 wire (ADR-0025)."""
        rows = repositories.list_queued_directives(
            session, brand_id=brand_id, activation_states=_ACTIONABLE_ACTIVATION_STATES
        )
        return [_directive(row).to_wire() for row in rows]

    @router.get("/ar/arms/{arm_id}/state", response_model=ArmStateOut)
    def get_arm_state(arm_id: str, _: Annotated[None, Depends(auth)],
                      session: Annotated[Session, Depends(dependency)]) -> ArmStateOut:
        row = repositories.load_arm_state(session, arm_id)
        if row is None:
            raise _not_found("arm", arm_id)
        return _arm_state(row, repositories.load_posterior(session, arm_id))

    @router.post("/ar/results", response_model=ResultAccepted)
    def post_result(payload: dict[str, Any], _: Annotated[None, Depends(auth)],
                    session: Annotated[Session, Depends(dependency)]) -> ResultAccepted:
        try:
            observation = parse_result_payload(payload)
        except ResultValidationError as exc:
            raise _invalid_result(exc) from exc
        state = repositories.load_arm_state(session, observation.arm_id)
        # An unknown arm carries no brand; `store_result` refuses it before the brand is
        # ever written (the "" is a placeholder the refusal path cannot reach).
        try:
            stored = store_result(
                session, payload,
                brand_id=str(state["brand_id"]) if state is not None else "",
                now=observation.observed_at,
            )
        except ResultValidationError as exc:
            raise _invalid_result(exc) from exc
        session.commit()
        return ResultAccepted(observation_id=stored.observation_id,
                              experiment_result_id=stored.experiment_result_id,
                              stored=stored.stored)

    @router.post("/ar/update", response_model=UpdateSummaryOut)
    def post_update(_: Annotated[None, Depends(auth)],
                    session: Annotated[Session, Depends(dependency)],
                    brand_id: str | None = None,
                    as_of: datetime | None = None) -> UpdateSummaryOut:
        """§74's worker over the stored observations. `brand_id` is accepted for operator
        symmetry with the other endpoints; the pending set is global and each observation
        applies at most once, so scoping would not change the outcome. Without `as_of`
        the worker applies everything stored to date by using the newest stored
        observation instant as `now` — deterministic, and never a clock read."""
        policy = repositories.load_active_policy(session)
        if policy is None:
            raise _conflict(
                "policy_not_configured",
                "no active policy row: activate a validated policy before applying updates "
                "(§92/§102); updates never run on code defaults",
            )
        moment = as_of if as_of is not None else repositories.latest_observation_instant(session)
        if moment is None:
            return UpdateSummaryOut(applied=0, skipped_duplicate=0, quarantined=0, failed=0)
        summary = apply_pending_updates(session, settings=settings, now=moment, policy=policy)
        session.commit()
        return UpdateSummaryOut(applied=summary.applied,
                                skipped_duplicate=summary.skipped_duplicate,
                                quarantined=summary.quarantined, failed=summary.failed)

    return router


def _dependencies(session_factory: SessionFactory | None) -> Callable[[], Iterator[Session]]:
    def session_dependency() -> Iterator[Session]:
        if session_factory is None:
            raise HTTPException(status_code=503, detail=ErrorShape(
                code="no_database", field_paths=[], message="no session factory configured",
            ).model_dump())
        with session_factory() as session:
            yield session

    return session_dependency


def _auth(settings: ARSettings) -> Callable[[str | None], None]:
    return require_service_token(settings.platform)


def _resolve_snapshot(request: CycleRequest, *, settings: ARSettings,
                      market_source: MarketSource | None) -> MarketSnapshot:
    """The P1 seam: the injected callable in tests; the real adapter in production.

    `MarketUnavailable` is a 503 — §102's stored fallback belongs to the scheduled job;
    the API refuses rather than serving a cycle built on data it could not read.
    """
    if market_source is not None:
        try:
            return market_source(request)
        except MarketUnavailable as exc:
            raise _unavailable(str(exc)) from exc
    token = (settings.platform.service_token.get_secret_value()
             if settings.platform is not None and settings.platform.service_token is not None
             else None)
    client = P1Client(settings.market_base_url, token,
                      timeout_seconds=settings.market_timeout_seconds)
    try:
        page = client.fetch_opportunities(limit=200)
    except MarketUnavailable as exc:
        raise _unavailable(str(exc)) from exc
    return MarketSnapshot(
        arms=tuple(adapt_opportunity(item, now=request.as_of) for item in page.items),
        as_of=page.as_of, source="live", score_version=page.score_version,
    )


def _summary(session: Session, result: CycleResult) -> CycleSummaryOut:
    row = repositories.load_cycle(session, result.cycle_id)
    if row is None:  # pragma: no cover - a finished cycle always has its stored row
        raise _not_found("cycle", result.cycle_id)
    return _summary_from_row(row, replay=result.replay)


def _summary_from_row(row: Mapping[str, Any], *, replay: bool) -> CycleSummaryOut:
    return CycleSummaryOut(
        cycle_id=str(row["id"]), status=str(row["status"]), replay=replay,
        activation_state=str(row["activation_state"]),
        slot_count=int(row["slot_count"]), budget=float(row["budget"]),
        p1_snapshot_time=_instant(row["p1_snapshot_time"]),
        selected_count=int(row["selected_count"]), decision_count=int(row["decision_count"]),
        rejected_count=int(row["rejected_count"]),
        warning_codes=list(row["warning_codes_json"] or []),
    )


def _decision(row: Mapping[str, Any]) -> DecisionOut:
    return DecisionOut(
        decision_id=str(row["id"]), cycle_id=str(row["cycle_id"]), arm_id=row["arm_id"],
        allocation_type=row["allocation_type"], sampled_reward=float(row["sampled_reward"]),
        posterior_expected_reward=float(row["posterior_expected_reward"]),
        selection_priority=float(row["selection_priority"]),
        rank_before_constraints=row["rank_before_constraints"],
        rank_after_constraints=row["rank_after_constraints"],
        constraint_adjustments=dict(row["constraint_adjustments_json"]),
        reason_codes=list(row["reason_codes"]), policy_version=row["policy_version"],
        model_version=row["model_version"], feature_version=row["feature_version"],
        normalization_version=row["normalization_version"],
        p1_score_version=row["p1_score_version"], created_at=_instant(row["created_at"]),
    )


def _directive(row: Mapping[str, Any]) -> ProductionDirective:
    """A stored directive row → the frozen C2 payload (the cycle's own read-back mapping)."""
    return ProductionDirective(
        directive_id=str(row["id"]), decision_id=str(row["decision_id"]),
        arm_id=row["arm_id"], allocation_type=row["allocation_type"],
        priority=row["priority"], quantity=row["quantity"], brief=row["brief_json"],
        expected_value=row["expected_value"], uncertainty=row["uncertainty"],
        budget_limit=row["budget_limit"], market_context=row["market_context_json"],
        selection_reason=tuple(row["selection_reason_json"]),
        directive_version=row["directive_version"], policy_version=row["policy_version"],
        expires_at=_instant(row["expires_at"]),
        exploration_fraction=row["exploration_fraction"], value_model=row["value_model"],
    )


def _arm_state(row: Mapping[str, Any], posterior: PosteriorState | None) -> ArmStateOut:
    return ArmStateOut(
        arm_id=row["arm_id"], brand_id=row["brand_id"], status=row["status"],
        selection_count=int(row["selection_count"]),
        mature_result_count=int(row["mature_result_count"]),
        provisional_result_count=int(row["provisional_result_count"]),
        recent_exposure_score=float(row["recent_exposure_score"]),
        cooldown_until=None if row["cooldown_until"] is None else _instant(row["cooldown_until"]),
        retirement_reason=row["retirement_reason"],
        last_updated_at=_instant(row["last_updated_at"]),
        state_version=int(row["state_version"]),
        posterior_model_version=None if posterior is None else posterior.model_version,
        posterior_mean=None if posterior is None else posterior.posterior_mean,
        posterior_variance=None if posterior is None else posterior.posterior_variance,
        observation_count=None if posterior is None else posterior.observation_count,
        update_version=None if posterior is None else posterior.update_version,
    )


def _instant(value: datetime) -> datetime:
    """SQLite round-trips `timezone=True` columns naive; the wire never is naive."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(status_code=404, detail=ErrorShape(
        code="not_found", field_paths=[kind], message=f"{kind} {identifier!r} does not exist",
    ).model_dump())


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=ErrorShape(
        code=code, field_paths=[], message=message,
    ).model_dump())


def _unavailable(message: str) -> HTTPException:
    return HTTPException(status_code=503, detail=ErrorShape(
        code="market_unavailable", field_paths=[], message=message,
    ).model_dump())


def _invalid_result(exc: ResultValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=ErrorShape(
        code="invalid_result", field_paths=list(exc.field_paths), message=str(exc),
    ).model_dump())

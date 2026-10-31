"""HTTP route handlers for `/experiments/v1` (PHASE-P3 §49, §81).

Auth first on every route; fail closed; no clock reads; deterministic writes.
No ``from __future__ import annotations`` here on purpose: FastAPI resolves
``Annotated[..., Depends(...)]`` metadata by evaluating the annotation, and the
dependencies are per-app closures that only the eager form can see.

Mutating routes (design, validate, materialize, extend) honour the ``Idempotency-Key``
header (:mod:`plm_platform.idempotency`): the same key and request replays the stored
answer; the same key with a different request is a 409 ``idempotency_key_reused``.
"""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from plm_contracts import ErrorShape
from plm_platform.clock import Clock, SystemClock
from plm_platform.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    IdempotencyStore,
    jsonable,
)
from sqlalchemy.orm import Session

from ..config import ExperimentsSettings
from ..domain.enums import ExperimentStatus
from ..domain.errors import (
    ExperimentStateError,
    LegacyExperimentError,
    LegacyPackageError,
    P3RefusalError,
)
from ..domain.experiment import Experiment, Treatment
from ..domain.package import ContentPackage
from ..experiments.engine import design_experiment
from ..genome.engine import build_genome_from_directive
from ..ingestion.p2_adapter import DirectiveValidationError, adapt_directive
from ..materialization.builder import (
    BudgetEnvelopeExceededError,
    PackageExtensionError,
    extend_package,
    materialize_package,
)
from ..storage import repositories
from ..storage.ids import deterministic_id
from ..storage.models import idempotency_records_table
from ..validation.engine import promote_to_validated, validate_experiment
from .schemas import (
    CreateExperimentRequest,
    CreateExperimentResponse,
    ExtendPackageRequest,
    ExtendPackageResponse,
    ListExperimentsResponse,
    MaterializePackageRequest,
    MaterializePackageResponse,
    UpdateStatusResponse,
    ValidateExperimentResponse,
)
from .service_token import require_service_token

__all__ = ["build_router"]

SessionFactory = Callable[[], Session]
IdempotencyKeyHeader = Annotated[str | None, Header(alias=IDEMPOTENCY_KEY_HEADER)]

_IDEMPOTENCY = IdempotencyStore(idempotency_records_table)


def build_router(
    *,
    session_factory: SessionFactory | None,
    settings: ExperimentsSettings,
    clock: Clock | None = None,
) -> APIRouter:
    """Build the experiments API router with fail-closed auth and session dependency."""
    router = APIRouter()
    service_clock: Clock = clock or SystemClock()
    dependency = _dependencies(session_factory)
    auth = _auth(settings)

    @router.post(
        "/experiments/v1/experiments",
        response_model=CreateExperimentResponse,
    )
    @router.post(
        "/experiments",
        response_model=CreateExperimentResponse,
        include_in_schema=False,
    )
    def create_experiment_endpoint(
        req: CreateExperimentRequest,
        request: Request,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> JSONResponse:
        """Create a structured experiment from an ingested P2 production directive."""

        def operation() -> tuple[int, Any]:
            try:
                adapted = adapt_directive(req.directive)
            except DirectiveValidationError as err:
                raise HTTPException(
                    status_code=422,
                    detail=ErrorShape(
                        code=err.error.code,
                        field_paths=[f"directive.{path}" for path in err.error.field_paths],
                        message=err.error.message,
                    ).model_dump(),
                ) from err

            try:
                base_genome = build_genome_from_directive(adapted)
                exp = design_experiment(
                    adapted,
                    base_genome,
                    created_at=service_clock.now(),
                    experiment_type=req.experiment_type,
                    target_variable=req.target_variable,
                    factors=req.factors,
                    factor_design=req.factor_design,
                    seed=req.seed,
                    control_only=settings.manual_supply,
                )
            except P3RefusalError as err:
                raise HTTPException(status_code=422, detail=err.error.model_dump()) from err

            gid = deterministic_id("gen", adapted.arm_id, adapted.directive_id)
            repositories.insert_genome(
                session, base_genome, genome_id=gid, created_at=service_clock.now()
            )
            repositories.insert_experiment(session, exp)
            return 200, _body(CreateExperimentResponse(experiment=exp))

        return _idempotent(session, request, idempotency_key, req, operation, service_clock)

    @router.get(
        "/experiments/v1/experiments",
        response_model=ListExperimentsResponse,
    )
    @router.get(
        "/experiments",
        response_model=ListExperimentsResponse,
        include_in_schema=False,
    )
    def list_experiments_endpoint(
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> ListExperimentsResponse:
        """List experiments ordered by creation time descending."""
        try:
            exps = repositories.list_experiments(session, limit=limit, offset=offset)
        except LegacyExperimentError as err:
            raise HTTPException(status_code=409, detail=err.error.model_dump()) from err
        return ListExperimentsResponse(experiments=exps, total=len(exps))

    @router.get(
        "/experiments/v1/experiments/{experiment_id}",
        response_model=Experiment,
    )
    @router.get(
        "/experiments/{experiment_id}",
        response_model=Experiment,
        include_in_schema=False,
    )
    def get_experiment_endpoint(
        experiment_id: str,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
    ) -> Experiment:
        """Retrieve an existing experiment definition by ID."""
        exp = _load_experiment(session, experiment_id)
        if exp is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorShape(
                    code="experiment_not_found",
                    field_paths=[experiment_id],
                    message=f"Experiment '{experiment_id}' does not exist",
                ).model_dump(),
            )
        return exp

    @router.get(
        "/experiments/v1/experiments/{experiment_id}/treatments",
        response_model=list[Treatment],
    )
    @router.get(
        "/experiments/{experiment_id}/treatments",
        response_model=list[Treatment],
        include_in_schema=False,
    )
    def get_treatments_endpoint(
        experiment_id: str,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
    ) -> list[Treatment]:
        """Retrieve treatments for an experiment."""
        exp = _load_experiment(session, experiment_id)
        if exp is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorShape(
                    code="experiment_not_found",
                    field_paths=[experiment_id],
                    message=f"Experiment '{experiment_id}' does not exist",
                ).model_dump(),
            )
        return list(exp.treatments)

    @router.post(
        "/experiments/v1/experiments/{experiment_id}/validate",
        response_model=ValidateExperimentResponse,
    )
    @router.post(
        "/experiments/{experiment_id}/validate",
        response_model=ValidateExperimentResponse,
        include_in_schema=False,
    )
    def validate_experiment_endpoint(
        experiment_id: str,
        request: Request,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> JSONResponse:
        """Run preflight validation on an experiment."""

        def operation() -> tuple[int, Any]:
            exp = _load_experiment(session, experiment_id)
            if exp is None:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(
                        code="experiment_not_found",
                        field_paths=[experiment_id],
                        message=f"Experiment '{experiment_id}' does not exist",
                    ).model_dump(),
                )

            genome = repositories.get_genome_by_fingerprint(session, exp.base_genome_fingerprint)
            if genome is None:
                raise HTTPException(
                    status_code=500,
                    detail=ErrorShape(
                        code="base_genome_missing",
                        field_paths=[exp.base_genome_fingerprint],
                        message="Base genome fingerprint could not be resolved from storage",
                    ).model_dump(),
                )

            report = validate_experiment(exp, genome)
            status = exp.status
            if report.passed and exp.status is ExperimentStatus.DRAFT:
                status = promote_to_validated(exp, report).status
                repositories.update_experiment_status(
                    session, experiment_id, status, now=service_clock.now()
                )
            return 200, _body(ValidateExperimentResponse(report=report, status=status))

        return _idempotent(session, request, idempotency_key, None, operation, service_clock)

    @router.post(
        "/experiments/v1/experiments/{experiment_id}/materialize",
        response_model=MaterializePackageResponse,
    )
    @router.post(
        "/experiments/{experiment_id}/materialize",
        response_model=MaterializePackageResponse,
        include_in_schema=False,
    )
    def materialize_package_endpoint(
        experiment_id: str,
        req: MaterializePackageRequest,
        request: Request,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> JSONResponse:
        """Materialize a specific experiment treatment into a complete ContentPackage.

        The package's playlist spec declares ``duration_range_seconds`` (the genome's
        playlist range) and the supply planned for it; the track count is not an input.
        """

        def operation() -> tuple[int, Any]:
            exp = _load_experiment(session, experiment_id)
            if exp is None:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(
                        code="experiment_not_found",
                        field_paths=[experiment_id],
                        message=f"Experiment '{experiment_id}' does not exist",
                    ).model_dump(),
                )

            treatment = next(
                (t for t in exp.treatments if t.treatment_id == req.treatment_id), None
            )
            if treatment is None:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(
                        code="treatment_not_found",
                        field_paths=[req.treatment_id],
                        message=(
                            f"Treatment '{req.treatment_id}' not found in experiment "
                            f"'{experiment_id}'"
                        ),
                    ).model_dump(),
                )

            genome = repositories.get_genome_by_fingerprint(session, exp.base_genome_fingerprint)
            if genome is None:
                raise HTTPException(
                    status_code=500,
                    detail=ErrorShape(
                        code="base_genome_missing",
                        field_paths=[exp.base_genome_fingerprint],
                        message="Base genome missing",
                    ).model_dump(),
                )

            try:
                package = materialize_package(
                    exp,
                    treatment,
                    genome,
                    created_at=service_clock.now(),
                    brand_id=req.brand_id,
                    budget_envelope=req.budget_envelope,
                    candidate_target=1 if settings.manual_supply else 2,
                )
            except ExperimentStateError as err:
                raise HTTPException(status_code=409, detail=err.error.model_dump()) from err
            except BudgetEnvelopeExceededError as err:
                raise HTTPException(
                    status_code=422,
                    detail=ErrorShape(
                        code="budget_envelope_exceeds_limit",
                        field_paths=["budget_envelope"],
                        message=str(err),
                    ).model_dump(),
                ) from err

            repositories.insert_content_package(session, package)
            repositories.update_experiment_status(
                session, experiment_id, ExperimentStatus.MATERIALIZED, now=service_clock.now()
            )
            return 200, _body(MaterializePackageResponse(package=package))

        return _idempotent(session, request, idempotency_key, req, operation, service_clock)

    @router.post(
        "/experiments/v1/experiments/{experiment_id}/cancel",
        response_model=UpdateStatusResponse,
    )
    @router.post(
        "/experiments/{experiment_id}/cancel",
        response_model=UpdateStatusResponse,
        include_in_schema=False,
    )
    def cancel_experiment_endpoint(
        experiment_id: str,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
    ) -> UpdateStatusResponse:
        """Cancel an experiment."""
        exp = _load_experiment(session, experiment_id)
        if exp is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorShape(
                    code="experiment_not_found",
                    field_paths=[experiment_id],
                    message=f"Experiment '{experiment_id}' does not exist",
                ).model_dump(),
            )
        repositories.update_experiment_status(
            session, experiment_id, ExperimentStatus.CANCELLED, now=service_clock.now()
        )
        session.commit()
        return UpdateStatusResponse(experiment_id=experiment_id, status=ExperimentStatus.CANCELLED.value)

    @router.post(
        "/experiments/v1/experiments/{experiment_id}/close",
        response_model=UpdateStatusResponse,
    )
    @router.post(
        "/experiments/{experiment_id}/close",
        response_model=UpdateStatusResponse,
        include_in_schema=False,
    )
    def close_experiment_endpoint(
        experiment_id: str,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
    ) -> UpdateStatusResponse:
        """Close an experiment."""
        exp = _load_experiment(session, experiment_id)
        if exp is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorShape(
                    code="experiment_not_found",
                    field_paths=[experiment_id],
                    message=f"Experiment '{experiment_id}' does not exist",
                ).model_dump(),
            )
        repositories.update_experiment_status(
            session, experiment_id, ExperimentStatus.COMPLETED, now=service_clock.now()
        )
        session.commit()
        return UpdateStatusResponse(experiment_id=experiment_id, status=ExperimentStatus.COMPLETED.value)

    @router.get(
        "/experiments/v1/packages/{package_id}",
        response_model=ContentPackage,
    )
    @router.get(
        "/content-packages/{package_id}",
        response_model=ContentPackage,
        include_in_schema=False,
    )
    def get_package_endpoint(
        package_id: str,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
    ) -> ContentPackage:
        """Retrieve an existing materialized ContentPackage by ID.

        Its ``playlist_spec`` carries the declared ``duration_range_seconds``; its
        ``track_specs`` include every top-up (``extensions``).
        """
        return _load_package(session, package_id)

    @router.post(
        "/experiments/v1/packages/{package_id}/extend",
        response_model=ExtendPackageResponse,
    )
    def extend_package_endpoint(
        package_id: str,
        req: ExtendPackageRequest,
        request: Request,
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> JSONResponse:
        """Top a package up with ``count`` more distinct track specs (R5).

        For a playlist P6 refused as ``playlist_supply_short``. ``req.idempotency_key``
        names the top-up: replaying it returns the same specs; the same key with another
        count is 409 ``idempotency_key_reused``; more than the package's declared
        ``max_extension_track_count`` in total is 409 ``package_extension_limit_exceeded``.
        """

        def operation() -> tuple[int, Any]:
            repositories.lock_content_package(session, package_id)
            package = _load_package(session, package_id)
            existing = repositories.get_package_extension(
                session, package_id, req.idempotency_key
            )
            if existing is not None:
                if existing.track_count != req.count:
                    raise HTTPException(
                        status_code=409,
                        detail=ErrorShape(
                            code="idempotency_key_reused",
                            field_paths=["idempotency_key"],
                            message=(
                                f"extension key {req.idempotency_key!r} already added "
                                f"{existing.track_count} specs; it cannot add {req.count}"
                            ),
                        ).model_dump(),
                    )
                by_id = {spec.track_spec_id: spec for spec in package.track_specs}
                extension, specs = existing, tuple(by_id[i] for i in existing.track_spec_ids)
                total = package.extension_track_count
            else:
                try:
                    extension, specs = extend_package(
                        package, count=req.count, idempotency_key=req.idempotency_key,
                        created_at=service_clock.now(),
                    )
                except PackageExtensionError as err:
                    raise HTTPException(status_code=409, detail=err.error.model_dump()) from err
                repositories.insert_package_extension(
                    session, extension, specs, first_index=len(package.track_specs)
                )
                total = package.extension_track_count + extension.track_count
            bound = package.playlist_spec.max_extension_track_count
            return 200, _body(ExtendPackageResponse(
                package_id=package_id,
                extension_id=extension.extension_id,
                idempotency_key=extension.idempotency_key,
                track_specs=specs,
                extension_track_count=total,
                max_extension_track_count=bound,
                remaining_extension_track_count=bound - total,
            ))

        return _idempotent(session, request, idempotency_key, req, operation, service_clock)

    return router


def _body(model: Any) -> Any:
    return jsonable(model.model_dump(mode="json"))


def _idempotent(
    session: Session,
    request: Request,
    key: str | None,
    body: Any,
    operation: Callable[[], tuple[int, Any]],
    clock: Clock,
) -> JSONResponse:
    """Run a mutation under the ``Idempotency-Key`` contract and answer with its JSON."""
    payload = None if body is None else body.model_dump(mode="json")
    try:
        stored = _IDEMPOTENCY.run(
            session, key, method=request.method, path=request.url.path, body=payload,
            now=clock.now, operation=operation,
        )
    except IdempotencyKeyReusedError as err:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=ErrorShape(
                code=err.code, field_paths=[IDEMPOTENCY_KEY_HEADER], message=str(err)
            ).model_dump(),
        ) from err
    except IdempotencyKeyInvalidError as err:
        raise HTTPException(
            status_code=422,
            detail=ErrorShape(
                code=err.code, field_paths=[IDEMPOTENCY_KEY_HEADER], message=str(err)
            ).model_dump(),
        ) from err
    return JSONResponse(status_code=stored.status_code, content=stored.body)


def _load_package(session: Session, package_id: str) -> ContentPackage:
    """A stored package; 404 when absent, 409 when it predates the declared range."""
    try:
        pkg = repositories.get_content_package(session, package_id)
    except LegacyPackageError as err:
        raise HTTPException(status_code=409, detail=err.error.model_dump()) from err
    if pkg is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorShape(
                code="package_not_found",
                field_paths=[package_id],
                message=f"ContentPackage '{package_id}' does not exist",
            ).model_dump(),
        )
    return pkg


def _load_experiment(session: Session, experiment_id: str) -> Experiment | None:
    """Load an experiment; a legacy row without a measurement plan is a 409, not a guess."""
    try:
        return repositories.get_experiment(session, experiment_id)
    except LegacyExperimentError as err:
        raise HTTPException(status_code=409, detail=err.error.model_dump()) from err


def _dependencies(session_factory: SessionFactory | None) -> Callable[[], Iterator[Session]]:
    def session_dependency() -> Iterator[Session]:
        if session_factory is None:
            raise HTTPException(
                status_code=503,
                detail=ErrorShape(
                    code="no_database",
                    field_paths=[],
                    message="no session factory configured",
                ).model_dump(),
            )
        with session_factory() as session:
            yield session

    return session_dependency


def _auth(settings: ExperimentsSettings) -> Callable[[str | None], None]:
    return require_service_token(settings.platform)

"""HTTP route handlers for `/experiments/v1` (PHASE-P3 §49, §81).

Auth first on every route; fail closed; no clock reads; deterministic writes.
No ``from __future__ import annotations`` here on purpose: FastAPI resolves
``Annotated[..., Depends(...)]`` metadata by evaluating the annotation, and the
dependencies are per-app closures that only the eager form can see.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from plm_contracts import ErrorShape
from plm_platform.clock import Clock, SystemClock
from sqlalchemy.orm import Session

from ..config import ExperimentsSettings
from ..domain.enums import ExperimentStatus
from ..domain.errors import ExperimentStateError, LegacyExperimentError, P3RefusalError
from ..domain.experiment import Experiment, Treatment
from ..domain.package import ContentPackage
from ..experiments.engine import design_experiment
from ..genome.engine import build_genome_from_directive
from ..ingestion.p2_adapter import DirectiveValidationError, adapt_directive
from ..materialization.builder import (
    BudgetEnvelopeExceededError,
    materialize_package,
)
from ..storage import repositories
from ..storage.ids import deterministic_id
from ..validation.engine import promote_to_validated, validate_experiment
from .schemas import (
    CreateExperimentRequest,
    CreateExperimentResponse,
    ListExperimentsResponse,
    MaterializePackageRequest,
    MaterializePackageResponse,
    UpdateStatusResponse,
    ValidateExperimentResponse,
)
from .service_token import require_service_token

__all__ = ["build_router"]

SessionFactory = Callable[[], Session]


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
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
    ) -> CreateExperimentResponse:
        """Create a structured experiment from an ingested P2 production directive."""
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
            )
        except P3RefusalError as err:
            raise HTTPException(status_code=422, detail=err.error.model_dump()) from err

        gid = deterministic_id("gen", adapted.arm_id, adapted.directive_id)
        repositories.insert_genome(
            session, base_genome, genome_id=gid, created_at=service_clock.now()
        )
        repositories.insert_experiment(session, exp)
        session.commit()

        return CreateExperimentResponse(experiment=exp)

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
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
    ) -> ValidateExperimentResponse:
        """Run preflight validation on an experiment."""
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
            session.commit()
        return ValidateExperimentResponse(report=report, status=status)

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
        _: Annotated[None, Depends(auth)],
        session: Annotated[Session, Depends(dependency)],
    ) -> MaterializePackageResponse:
        """Materialize a specific experiment treatment into a complete ContentPackage."""
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

        treatment = next((t for t in exp.treatments if t.treatment_id == req.treatment_id), None)
        if treatment is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorShape(
                    code="treatment_not_found",
                    field_paths=[req.treatment_id],
                    message=f"Treatment '{req.treatment_id}' not found in experiment '{experiment_id}'",
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
                track_count=req.track_count,
                total_duration_minutes=req.total_duration_minutes,
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
        session.commit()

        return MaterializePackageResponse(package=package)

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
        """Retrieve an existing materialized ContentPackage by ID."""
        pkg = repositories.get_content_package(session, package_id)
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

    return router


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

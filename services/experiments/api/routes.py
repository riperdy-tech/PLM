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
from sqlalchemy.orm import Session

from ..config import ExperimentsSettings
from ..domain.enums import ExperimentStatus
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
from ..validation.engine import validate_experiment
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
) -> APIRouter:
    """Build the experiments API router with fail-closed auth and session dependency."""
    router = APIRouter()
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
                    code="directive_validation_error",
                    field_paths=["directive"],
                    message=str(err),
                ).model_dump(),
            ) from err

        base_genome = build_genome_from_directive(adapted)
        gid = deterministic_id("gen", adapted.arm_id, adapted.directive_id)
        repositories.insert_genome(session, base_genome, genome_id=gid)

        exp = design_experiment(
            adapted,
            base_genome,
            experiment_type=req.experiment_type,
            target_variable=req.target_variable,
            seed=req.seed,
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
        exps = repositories.list_experiments(session, limit=limit, offset=offset)
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
        exp = repositories.get_experiment(session, experiment_id)
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
        exp = repositories.get_experiment(session, experiment_id)
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
        exp = repositories.get_experiment(session, experiment_id)
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
        return ValidateExperimentResponse(report=report)

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
        exp = repositories.get_experiment(session, experiment_id)
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
                brand_id=req.brand_id,
                budget_envelope=req.budget_envelope,
                track_count=req.track_count,
                total_duration_minutes=req.total_duration_minutes,
            )
        except BudgetEnvelopeExceededError as err:
            raise HTTPException(
                status_code=422,
                detail=ErrorShape(
                    code="budget_envelope_error",
                    field_paths=["budget_envelope"],
                    message=str(err),
                ).model_dump(),
            ) from err

        repositories.insert_content_package(session, package)
        repositories.update_experiment_status(session, experiment_id, ExperimentStatus.MATERIALIZED)
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
        exp = repositories.get_experiment(session, experiment_id)
        if exp is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorShape(
                    code="experiment_not_found",
                    field_paths=[experiment_id],
                    message=f"Experiment '{experiment_id}' does not exist",
                ).model_dump(),
            )
        repositories.update_experiment_status(session, experiment_id, ExperimentStatus.CANCELLED)
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
        exp = repositories.get_experiment(session, experiment_id)
        if exp is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorShape(
                    code="experiment_not_found",
                    field_paths=[experiment_id],
                    message=f"Experiment '{experiment_id}' does not exist",
                ).model_dump(),
            )
        repositories.update_experiment_status(session, experiment_id, ExperimentStatus.COMPLETED)
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

"""API Request and Response schemas for `/experiments/v1` (PHASE-P3 §49, §81)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import ExperimentType
from ..domain.experiment import Experiment
from ..domain.package import ContentPackage
from ..validation.engine import ValidationReport


class CreateExperimentRequest(BaseModel):
    """Payload to create an experiment from a P2 production directive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directive: dict[str, Any]
    experiment_type: ExperimentType = ExperimentType.SINGLE_FACTOR
    target_variable: str | None = None
    seed: int = 0


class CreateExperimentResponse(BaseModel):
    """Response containing the created experiment definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment: Experiment


class ValidateExperimentResponse(BaseModel):
    """Response containing the preflight validation report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report: ValidationReport


class MaterializePackageRequest(BaseModel):
    """Request to materialize a specific treatment of an experiment into a ContentPackage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    treatment_id: str
    brand_id: str = "neon_seoul"
    budget_envelope: float = Field(default=100.0, ge=0.0)
    track_count: int = Field(default=5, ge=1)
    total_duration_minutes: int = Field(default=60, ge=1)


class MaterializePackageResponse(BaseModel):
    """Response containing the materialized ContentPackage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package: ContentPackage


class ListExperimentsResponse(BaseModel):
    """Response containing list of experiments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiments: list[Experiment]
    total: int


class UpdateStatusResponse(BaseModel):
    """Response acknowledging status update."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str
    status: str

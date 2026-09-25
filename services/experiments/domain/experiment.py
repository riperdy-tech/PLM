"""Experiment domain models (PHASE-P3 §12–§18, §30)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import ExperimentStatus, ExperimentType, VariableClass


class Hypothesis(BaseModel):
    """The testable causal hypothesis behind an experiment (§13)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str
    target_metric: str
    expected_direction: str = "increase"
    rationale: str = ""


class ExperimentVariable(BaseModel):
    """A variable deliberately configured or tracked in an experiment (§14)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    variable_class: VariableClass
    baseline_value: Any
    treatment_values: tuple[Any, ...] = ()


class Treatment(BaseModel):
    """A specific variation/arm within an experiment (§36)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    treatment_id: str
    treatment_index: int
    name: str
    variable_assignments: dict[str, Any]
    genome_delta: dict[str, Any] = Field(default_factory=dict)


class Experiment(BaseModel):
    """Immutable experiment definition record (§12, §30)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str
    content_arm_id: str
    production_directive_id: str
    experiment_type: ExperimentType
    status: ExperimentStatus
    hypothesis: Hypothesis
    variables: tuple[ExperimentVariable, ...] = ()
    treatments: tuple[Treatment, ...] = ()
    base_genome_fingerprint: str
    experiment_version: str = "p3-exp-v1"
    created_at: datetime
    immutable_after_start: bool = True

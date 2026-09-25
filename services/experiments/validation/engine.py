"""Validation Engine: contradiction detection, duplicate prevention, and budget verification (PHASE-P3 §29, §33, §34, §38)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..domain.enums import VariableClass, VocalGender, VocalMode

if TYPE_CHECKING:
    from ..domain.experiment import Experiment
    from ..domain.genome import ContentGenome


class ValidationReport(BaseModel):
    """Structured report returned by experiment preflight validation (§32)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()


def validate_experiment(
    experiment: Experiment,
    base_genome: ContentGenome,
    *,
    budget_limit: float | None = None,
) -> ValidationReport:
    """Validate an experiment design prior to materialization and production execution."""
    errors: list[str] = []
    warnings: list[str] = []
    contradictions: list[str] = []

    # 1. Budget validation
    if budget_limit is not None and budget_limit <= 0.0:
        errors.append(f"Budget ceiling must be positive; got {budget_limit}")

    # 2. Contradiction detection (§33)
    if (
        base_genome.vocal_mode == VocalMode.INSTRUMENTAL
        and base_genome.vocal_gender != VocalGender.NONE
    ):
        contradictions.append("Instrumental genome cannot specify a non-empty vocal gender")

    # 3. Duplicate treatment prevention (§29)
    seen_assignments: set[str] = set()
    for trt in experiment.treatments:
        canon_assignments = json.dumps(trt.variable_assignments, sort_keys=True)
        if canon_assignments in seen_assignments:
            errors.append(
                f"Duplicate treatment detected: '{trt.name}' has identical variable assignments"
            )
        seen_assignments.add(canon_assignments)

    # 4. Contamination & uncontrolled variable detection (§38)
    exp_var_names = {
        v.name for v in experiment.variables if v.variable_class == VariableClass.EXPERIMENTAL
    }
    for trt in experiment.treatments:
        if trt.treatment_index == 0:
            continue  # control treatment reflects baseline
        for mutated_key in trt.variable_assignments:
            if mutated_key not in exp_var_names:
                errors.append(
                    f"Treatment '{trt.name}' mutates undeclared or uncontrolled variable '{mutated_key}'"
                )

    passed = len(errors) == 0 and len(contradictions) == 0

    return ValidationReport(
        passed=passed,
        errors=tuple(errors),
        warnings=tuple(warnings),
        contradictions=tuple(contradictions),
    )

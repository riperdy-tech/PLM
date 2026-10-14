"""Validation Engine: preflight checks and the only path to ``VALIDATED`` (P3 §29–§38).

``validate_experiment`` produces a report; ``promote_to_validated`` is the only function
that sets ``ExperimentStatus.VALIDATED``, and only from ``DRAFT`` with a passing report
for the same experiment. Design never self-certifies.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..domain.enums import ExperimentStatus, FactorDesign, VariableClass, VocalGender, VocalMode
from ..domain.errors import ExperimentStateError

if TYPE_CHECKING:
    from ..domain.experiment import Experiment
    from ..domain.genome import ContentGenome

__all__ = ["ValidationReport", "promote_to_validated", "validate_experiment"]


class ValidationReport(BaseModel):
    """Structured report returned by experiment preflight validation (§32)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str
    passed: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()


def _canon(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def validate_experiment(experiment: Experiment, base_genome: ContentGenome) -> ValidationReport:
    """Validate an experiment design prior to materialization and production execution."""
    errors: list[str] = []
    warnings: list[str] = []
    contradictions: list[str] = []

    # 1. Budget ceiling from the P2 directive (§34)
    if experiment.budget_limit <= 0.0:
        errors.append(f"Budget ceiling must be positive; got {experiment.budget_limit}")

    # 2. Contradiction detection (§33)
    if (
        base_genome.vocal_mode == VocalMode.INSTRUMENTAL
        and base_genome.vocal_gender != VocalGender.NONE
    ):
        contradictions.append("Instrumental genome cannot specify a non-empty vocal gender")

    # 3. Duplicate treatment prevention (§29)
    seen_assignments: set[str] = set()
    for trt in experiment.treatments:
        canon_assignments = _canon(trt.variable_assignments)
        if canon_assignments in seen_assignments:
            errors.append(
                f"Duplicate treatment detected: '{trt.name}' has identical variable assignments"
            )
        seen_assignments.add(canon_assignments)

    # 4. Contamination & uncontrolled variable detection (§38)
    exp_var_names = {
        v.name for v in experiment.variables if v.variable_class == VariableClass.EXPERIMENTAL
    }
    control = next((t for t in experiment.treatments if t.treatment_index == 0), None)
    if control is None:
        errors.append("Experiment has no control treatment (index 0)")
    elif control.genome_delta:
        errors.append("Control treatment must not change the base genome")
    for trt in experiment.treatments:
        if trt.treatment_index == 0:
            continue
        for mutated_key in sorted(set(trt.variable_assignments) | set(trt.genome_delta)):
            if mutated_key not in exp_var_names:
                errors.append(
                    f"Treatment '{trt.name}' mutates undeclared or uncontrolled variable "
                    f"'{mutated_key}'"
                )
        if control is None:
            continue
        differs = sorted(
            k
            for k in set(trt.variable_assignments) | set(control.variable_assignments)
            if _canon(trt.variable_assignments.get(k)) != _canon(control.variable_assignments.get(k))
        )
        if not differs:
            errors.append(f"Treatment '{trt.name}' does not differ from control")
        if (
            experiment.factor_design is FactorDesign.ONE_FACTOR_PER_TREATMENT
            and len(differs) > 1
        ):
            errors.append(
                f"Treatment '{trt.name}' changes {differs} together; a "
                "one_factor_per_treatment design changes exactly one variable (contamination)"
            )
        if sorted(trt.genome_delta) != differs:
            errors.append(
                f"Treatment '{trt.name}' genome_delta {sorted(trt.genome_delta)} does not match "
                f"its differences from control {differs}"
            )

    passed = len(errors) == 0 and len(contradictions) == 0

    return ValidationReport(
        experiment_id=experiment.experiment_id,
        passed=passed,
        errors=tuple(errors),
        warnings=tuple(warnings),
        contradictions=tuple(contradictions),
    )


def promote_to_validated(experiment: Experiment, report: ValidationReport) -> Experiment:
    """Return the experiment marked ``VALIDATED``; refuse unless its own report passed."""
    if report.experiment_id != experiment.experiment_id:
        raise ExperimentStateError(
            "validation_report_mismatch",
            f"Report for {report.experiment_id!r} cannot validate {experiment.experiment_id!r}",
            field_paths=["experiment_id"],
        )
    if not report.passed:
        raise ExperimentStateError(
            "validation_failed",
            f"Experiment {experiment.experiment_id!r} failed validation: "
            f"{list(report.errors) + list(report.contradictions)}",
        )
    if experiment.status is ExperimentStatus.VALIDATED:
        return experiment
    if experiment.status is not ExperimentStatus.DRAFT:
        raise ExperimentStateError(
            "invalid_status_transition",
            f"Cannot move experiment from {experiment.status.value!r} to 'validated'",
            field_paths=["status"],
        )
    return experiment.model_copy(update={"status": ExperimentStatus.VALIDATED})

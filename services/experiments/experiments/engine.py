"""Experiment Engine: design, variable classification, and treatment assignment (P3 §12–§18, §36).

Design rules:

* **One factor at a time by default.** A single-factor experiment (and every type other
  than ``multi_factor``) varies exactly one variable; each treatment equals control in
  every other dimension.
* **Multi-factor is explicit.** ``multi_factor`` requires declared ``factors`` (two or
  more) and a :class:`FactorDesign`:
  ``one_factor_per_treatment`` (each treatment differs from control in exactly one
  declared factor, factors take turns) or ``full_factorial`` (two levels per factor,
  every combination). Two variables never "move together" outside a stated factorial.
* **Distinct treatments.** Values come from the ontology-driven registry in
  :mod:`.values`. If fewer distinct values exist than requested treatments, fewer
  treatments are designed and the reason is recorded in ``design_notes``; nothing is
  duplicated and nothing is a placeholder.
* **Draft until validated.** Design returns ``DRAFT``. Only
  :func:`~services.experiments.validation.engine.promote_to_validated` sets
  ``VALIDATED``, and only for a passing report.
"""

from __future__ import annotations

from datetime import datetime
from itertools import product
from typing import TYPE_CHECKING, Any, Final

from ..domain.enums import ExperimentStatus, ExperimentType, FactorDesign, VariableClass
from ..domain.errors import ExperimentDesignError
from ..domain.experiment import (
    Experiment,
    ExperimentVariable,
    Hypothesis,
    RandomizationRecord,
    Treatment,
)
from ..domain.fingerprint import compute_genome_fingerprint
from ..domain.measurement import build_default_measurement_plan
from ..genome.engine import CreativeLatitudeEnvelope, TemplateLibrary, default_template_library
from ..storage.ids import experiment_id, treatment_id
from .values import (
    RANDOMIZATION_ALGORITHM,
    RANDOMIZATION_VERSION,
    baseline_value,
    candidate_values,
    canonical,
)

if TYPE_CHECKING:
    from ..domain.genome import ContentGenome
    from ..ingestion.p2_adapter import AdaptedDirective

__all__ = ["DEFAULT_TARGET_VARIABLE", "design_experiment"]

DEFAULT_TARGET_VARIABLE: Final[str] = "bpm"
DEFAULT_TARGET_METRIC: Final[str] = "retention_30s"


def _declared_factors(
    exp_type: ExperimentType,
    target_variable: str | None,
    factors: tuple[str, ...] | None,
    factor_design: FactorDesign | None,
) -> tuple[tuple[str, ...], FactorDesign]:
    if exp_type is ExperimentType.MULTI_FACTOR:
        if target_variable is not None:
            raise ExperimentDesignError(
                "target_variable_with_multi_factor",
                "A multi-factor experiment declares its variables in 'factors', not "
                "'target_variable'",
                field_paths=["target_variable"],
            )
        if factors is None or len(factors) < 2:
            raise ExperimentDesignError(
                "multi_factor_requires_factors",
                "A multi-factor experiment must declare two or more factors explicitly",
                field_paths=["factors"],
            )
        declared = tuple(factors)
        design = FactorDesign(factor_design or FactorDesign.ONE_FACTOR_PER_TREATMENT)
    else:
        if factors is not None:
            raise ExperimentDesignError(
                "factors_require_multi_factor",
                f"'factors' is only valid for multi_factor experiments, not {exp_type.value!r}; "
                "use 'target_variable'",
                field_paths=["factors"],
            )
        if factor_design not in (None, FactorDesign.ONE_FACTOR_PER_TREATMENT):
            raise ExperimentDesignError(
                "factor_design_requires_multi_factor",
                f"{exp_type.value!r} experiments vary one factor per treatment",
                field_paths=["factor_design"],
            )
        declared = (target_variable or DEFAULT_TARGET_VARIABLE,)
        design = FactorDesign.ONE_FACTOR_PER_TREATMENT
    if len(set(declared)) != len(declared):
        raise ExperimentDesignError(
            "duplicate_factor", f"Factors repeat a variable: {list(declared)}", field_paths=["factors"]
        )
    return declared, design


def _one_factor_per_treatment(
    factors: tuple[str, ...], candidates: dict[str, tuple[Any, ...]], variants: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Factors take turns; each treatment overrides exactly one factor with its next value."""
    if variants < len(factors):
        raise ExperimentDesignError(
            "multi_factor_needs_slot_per_factor",
            f"{len(factors)} factors need at least {len(factors)} treatment slots; the "
            f"directive allows {variants}",
            field_paths=["requested_experiment_count"],
        )
    cursors = dict.fromkeys(factors, 0)
    overrides: list[dict[str, Any]] = []
    while len(overrides) < variants:
        progressed = False
        for factor in factors:
            if len(overrides) == variants:
                break
            if cursors[factor] < len(candidates[factor]):
                overrides.append({factor: candidates[factor][cursors[factor]]})
                cursors[factor] += 1
                progressed = True
        if not progressed:
            break
    notes: list[str] = []
    if len(overrides) < variants:
        notes.append(f"treatments_reduced:requested={variants},distinct={len(overrides)}")
        notes.extend(
            f"values_exhausted:{factor}:available={len(candidates[factor])}" for factor in factors
        )
    return overrides, notes


def _full_factorial(
    factors: tuple[str, ...], candidates: dict[str, tuple[Any, ...]], variants: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Two levels per factor (control, first alternative); every non-control cell."""
    cells = 2 ** len(factors) - 1
    if variants < cells:
        raise ExperimentDesignError(
            "factorial_needs_more_slots",
            f"A full factorial over {len(factors)} factors needs {cells} treatment slots; "
            f"the directive allows {variants}",
            field_paths=["requested_experiment_count"],
        )
    overrides: list[dict[str, Any]] = []
    for levels in product((False, True), repeat=len(factors)):
        if not any(levels):
            continue
        overrides.append(
            {factor: candidates[factor][0] for factor, on in zip(factors, levels, strict=True) if on}
        )
    notes = [f"treatments_reduced:requested={variants},factorial_cells={cells}"] if variants > cells else []
    return overrides, notes


def design_experiment(
    directive: AdaptedDirective,
    base_genome: ContentGenome,
    *,
    experiment_type: ExperimentType | None = None,
    target_variable: str | None = None,
    factors: tuple[str, ...] | None = None,
    factor_design: FactorDesign | None = None,
    seed: int = 0,
    created_at: datetime,
    library: TemplateLibrary | None = None,
    control_only: bool = False,
) -> Experiment:
    """Design a reproducible DRAFT experiment from an adapted P2 directive.

    ``control_only`` designs no treatments (manual supply): the run produces the control
    package alone, and ``design_notes`` records why.
    """
    exp_type = experiment_type or ExperimentType.SINGLE_FACTOR
    declared, design = _declared_factors(exp_type, target_variable, factors, factor_design)

    template = (library or default_template_library()).for_genre(base_genome.genre)
    envelope = CreativeLatitudeEnvelope.default_for_exploration(directive.exploration_level)
    candidates = {
        factor: candidate_values(
            factor,
            genome=base_genome,
            envelope=envelope,
            alternatives=template.alternatives,
            directive_id=directive.directive_id,
            seed=seed,
        )
        for factor in declared
    }

    variants = max(1, directive.requested_experiment_count - 1)
    overrides: list[dict[str, Any]]
    if control_only:
        overrides, notes = [], ["control_only:manual_supply"]
    elif design is FactorDesign.FULL_FACTORIAL:
        overrides, notes = _full_factorial(declared, candidates, variants)
    else:
        overrides, notes = _one_factor_per_treatment(declared, candidates, variants)

    exp_id = experiment_id(
        directive.arm_id,
        directive.directive_id,
        exp_type.value,
        design.value,
        ",".join(declared),
        str(seed),
    )
    control = {factor: baseline_value(base_genome, factor) for factor in declared}
    treatments = [
        Treatment(
            treatment_id=treatment_id(exp_id, 0),
            treatment_index=0,
            name="control",
            variable_assignments=dict(control),
            genome_delta={},
        )
    ]
    for index, override in enumerate(overrides, start=1):
        treatments.append(
            Treatment(
                treatment_id=treatment_id(exp_id, index),
                treatment_index=index,
                name=f"treatment_{index}",
                variable_assignments={**control, **override},
                genome_delta=dict(override),
            )
        )
    keys = [canonical(t.variable_assignments) for t in treatments]
    if len(set(keys)) != len(keys):  # the generators guarantee this; refuse rather than ship
        raise ExperimentDesignError(
            "duplicate_treatment", "Designed treatments are not pairwise distinct"
        )

    variables = [
        ExperimentVariable(
            name="genre", variable_class=VariableClass.FIXED, baseline_value=base_genome.genre
        ),
        ExperimentVariable(
            name="language", variable_class=VariableClass.FIXED, baseline_value=base_genome.language
        ),
        ExperimentVariable(
            name="track_duration",
            variable_class=VariableClass.CONTROLLED,
            baseline_value=base_genome.track_duration.target_seconds,
        ),
        ExperimentVariable(
            name="playlist_duration",
            variable_class=VariableClass.CONTROLLED,
            baseline_value=base_genome.playlist_duration.target_seconds,
        ),
    ]
    for factor in declared:
        used: list[Any] = []
        for override in overrides:
            if factor in override and override[factor] not in used:
                used.append(override[factor])
        variables.append(
            ExperimentVariable(
                name=factor,
                variable_class=VariableClass.EXPERIMENTAL,
                baseline_value=control[factor],
                treatment_values=tuple(used),
            )
        )

    hypothesis = Hypothesis(
        statement=(
            f"Varying {' and '.join(declared)} ({design.value}) changes "
            f"{DEFAULT_TARGET_METRIC} for arm {directive.arm_id}"
        ),
        target_metric=DEFAULT_TARGET_METRIC,
        expected_direction="increase",
        rationale=(
            f"Exploration level: {directive.exploration_level}; value model: "
            f"{directive.value_model}"
        ),
    )

    return Experiment(
        experiment_id=exp_id,
        content_arm_id=directive.arm_id,
        production_directive_id=directive.directive_id,
        experiment_type=exp_type,
        status=ExperimentStatus.DRAFT,
        hypothesis=hypothesis,
        variables=tuple(variables),
        treatments=tuple(treatments),
        base_genome_fingerprint=compute_genome_fingerprint(base_genome),
        experiment_version="p3-exp-v2",
        created_at=created_at,
        factor_design=design,
        measurement_plan=build_default_measurement_plan(
            exp_id,
            outcome_horizon=directive.outcome_horizon,
            target_metric=DEFAULT_TARGET_METRIC,
        ),
        budget_limit=directive.budget_limit,
        randomization=RandomizationRecord(
            seed=seed, algorithm=RANDOMIZATION_ALGORITHM, version=RANDOMIZATION_VERSION
        ),
        design_notes=tuple(notes),
    )

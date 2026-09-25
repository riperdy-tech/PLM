"""Experiment Engine: design, variable classification, and treatment assignment (PHASE-P3 §12–§18, §36)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..domain.enums import ExperimentStatus, ExperimentType, VariableClass
from ..domain.experiment import (
    Experiment,
    ExperimentVariable,
    Hypothesis,
    Treatment,
)
from ..domain.fingerprint import compute_genome_fingerprint
from ..genome.engine import CreativeLatitudeEnvelope
from ..storage.ids import experiment_id, treatment_id

if TYPE_CHECKING:
    from ..domain.genome import ContentGenome
    from ..ingestion.p2_adapter import AdaptedDirective


def design_experiment(
    directive: AdaptedDirective,
    base_genome: ContentGenome,
    *,
    experiment_type: ExperimentType | None = None,
    target_variable: str | None = None,
    seed: int = 0,
    created_at: datetime | None = None,
) -> Experiment:
    """Design a structured, reproducible experiment from an adapted P2 directive."""
    now = created_at or datetime.now(UTC)
    _ = seed

    # 1. Determine experiment type
    if experiment_type is None:
        if directive.exploration_level == "radical_explore":
            exp_type = ExperimentType.MULTI_FACTOR
        else:
            exp_type = ExperimentType.SINGLE_FACTOR
    else:
        exp_type = experiment_type

    # 2. Select experimental variables
    latitude = CreativeLatitudeEnvelope.default_for_exploration(directive.exploration_level)

    if exp_type == ExperimentType.MULTI_FACTOR:
        candidate_vars = ["bpm", "visual_world", "energy"]
        exp_var_names = candidate_vars[:2]
    else:
        exp_var_names = [target_variable or "bpm"]

    variables: list[ExperimentVariable] = []

    # Fixed hard constraints
    variables.append(
        ExperimentVariable(
            name="genre",
            variable_class=VariableClass.FIXED,
            baseline_value=base_genome.genre,
        )
    )
    variables.append(
        ExperimentVariable(
            name="language",
            variable_class=VariableClass.FIXED,
            baseline_value=base_genome.language,
        )
    )

    # Controlled variables
    variables.append(
        ExperimentVariable(
            name="track_duration",
            variable_class=VariableClass.CONTROLLED,
            baseline_value=base_genome.track_duration.target_seconds,
        )
    )
    variables.append(
        ExperimentVariable(
            name="playlist_duration",
            variable_class=VariableClass.CONTROLLED,
            baseline_value=base_genome.playlist_duration.target_seconds,
        )
    )

    # 3. Create treatments & experimental variable specs
    treatments: list[Treatment] = []
    exp_id = experiment_id(directive.arm_id, directive.directive_id, exp_type.value)

    # Control treatment (index 0)
    control_assignments = {v: getattr(base_genome, v) for v in exp_var_names}
    treatments.append(
        Treatment(
            treatment_id=treatment_id(exp_id, 0),
            treatment_index=0,
            name="control",
            variable_assignments={
                k: (v.model_dump() if hasattr(v, "model_dump") else v)
                for k, v in control_assignments.items()
            },
            genome_delta={},
        )
    )

    # Variant treatments
    requested_variants = max(1, directive.requested_experiment_count - 1)
    treatment_values_by_var: dict[str, list[Any]] = {v: [] for v in exp_var_names}

    for idx in range(1, requested_variants + 1):
        trt_assignments: dict[str, Any] = {}
        genome_delta: dict[str, Any] = {}

        for var_name in exp_var_names:
            if var_name == "bpm":
                base_bpm = base_genome.bpm.target_bpm or 80
                shift = int(base_bpm * latitude.bpm_variance_pct)
                variant_bpm = base_bpm + (shift if idx % 2 == 1 else -shift)
                delta_bpm = {
                    "min_bpm": variant_bpm - 5,
                    "max_bpm": variant_bpm + 5,
                    "target_bpm": variant_bpm,
                }
                trt_assignments["bpm"] = delta_bpm
                genome_delta["bpm"] = delta_bpm
                treatment_values_by_var["bpm"].append(delta_bpm)

            elif var_name == "visual_world":
                available = [
                    w for w in latitude.allowed_visual_worlds if w != base_genome.visual_world
                ]
                chosen_world = (
                    available[idx % len(available)] if available else base_genome.visual_world
                )
                trt_assignments["visual_world"] = chosen_world
                genome_delta["visual_world"] = chosen_world
                treatment_values_by_var["visual_world"].append(chosen_world)

            else:
                # default incidental/experimental bump
                trt_assignments[var_name] = f"variant_{idx}"
                genome_delta[var_name] = f"variant_{idx}"
                treatment_values_by_var[var_name].append(f"variant_{idx}")

        treatments.append(
            Treatment(
                treatment_id=treatment_id(exp_id, idx),
                treatment_index=idx,
                name=f"treatment_{idx}",
                variable_assignments=trt_assignments,
                genome_delta=genome_delta,
            )
        )

    # Add experimental variables
    for var_name in exp_var_names:
        baseline_val = getattr(base_genome, var_name)
        if hasattr(baseline_val, "model_dump"):
            baseline_val = baseline_val.model_dump()
        variables.append(
            ExperimentVariable(
                name=var_name,
                variable_class=VariableClass.EXPERIMENTAL,
                baseline_value=baseline_val,
                treatment_values=tuple(treatment_values_by_var[var_name]),
            )
        )

    hyp_target = exp_var_names[0]
    hypothesis = Hypothesis(
        statement=f"Manipulating {hyp_target} optimizes audience retention for {directive.arm_id}",
        target_metric="retention_30s",
        expected_direction="increase",
        rationale=f"Exploration level: {directive.exploration_level}; value model: {directive.value_model}",
    )

    return Experiment(
        experiment_id=exp_id,
        content_arm_id=directive.arm_id,
        production_directive_id=directive.directive_id,
        experiment_type=exp_type,
        status=ExperimentStatus.VALIDATED,
        hypothesis=hypothesis,
        variables=tuple(variables),
        treatments=tuple(treatments),
        base_genome_fingerprint=compute_genome_fingerprint(base_genome),
        experiment_version="p3-exp-v1",
        created_at=now,
    )

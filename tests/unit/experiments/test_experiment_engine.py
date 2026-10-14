"""Tests for P3 Experiment Engine, variable assignment, and treatments (Task P3.6)."""

from __future__ import annotations

from datetime import UTC, datetime

from services.experiments.domain.enums import (
    ExperimentStatus,
    ExperimentType,
    VariableClass,
)
from services.experiments.experiments.engine import design_experiment
from services.experiments.genome.engine import build_genome_from_directive
from services.experiments.ingestion.p2_adapter import AdaptedDirective

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _sample_directive(exploration_level: str = "exploit") -> AdaptedDirective:
    return AdaptedDirective(
        directive_id="dir_sample_01",
        decision_id="dec_sample_01",
        arm_id="rnb_late_night_drive",
        exploration_level=exploration_level,
        priority=1,
        requested_experiment_count=2,
        production_deadline=NOW,
        budget_limit=200.0,
        brief={
            "genre": "rnb",
            "mood": ["melancholic", "dreamy"],
            "use_case": ["late_night_drive"],
            "language": "english",
            "vocal_mode": "vocal",
            "visual_world": "rainy_asian_city",
        },
        required_constraints={"genre": "rnb", "language": "english"},
        recommended_attributes={"mood": ["melancholic", "dreamy"]},
        market_context={"brand_id": "neon_seoul"},
        selection_reason=["test"],
        expected_value=0.85,
        uncertainty=0.15,
        value_model="p2-proxy-v1",
        directive_version="p2-v1",
        policy_version="p2-v1",
    )


def test_design_single_factor_experiment() -> None:
    directive = _sample_directive()
    base_genome = build_genome_from_directive(directive)

    exp = design_experiment(
        directive,
        base_genome,
        experiment_type=ExperimentType.SINGLE_FACTOR,
        target_variable="bpm",
        seed=42,
        created_at=FIXED_NOW,
    )

    assert exp.experiment_type == ExperimentType.SINGLE_FACTOR
    assert exp.status == ExperimentStatus.VALIDATED
    assert exp.content_arm_id == directive.arm_id
    assert exp.production_directive_id == directive.directive_id
    assert len(exp.treatments) >= 2
    assert exp.treatments[0].name == "control"
    assert exp.treatments[1].name == "treatment_1"

    # Check variable classification
    var_map = {v.name: v.variable_class for v in exp.variables}
    assert var_map["bpm"] == VariableClass.EXPERIMENTAL
    assert var_map["genre"] == VariableClass.FIXED


def test_experiment_design_is_deterministic() -> None:
    directive = _sample_directive()
    base_genome = build_genome_from_directive(directive)

    exp1 = design_experiment(directive, base_genome, seed=123, created_at=FIXED_NOW)
    exp2 = design_experiment(directive, base_genome, seed=123, created_at=FIXED_NOW)

    assert exp1.experiment_id == exp2.experiment_id
    assert exp1.treatments == exp2.treatments
    assert exp1.base_genome_fingerprint == exp2.base_genome_fingerprint


def test_radical_exploration_enables_multi_factor() -> None:
    directive = _sample_directive(exploration_level="radical_explore")
    base_genome = build_genome_from_directive(directive)

    exp = design_experiment(
        directive,
        base_genome,
        experiment_type=ExperimentType.MULTI_FACTOR,
        seed=999,
        created_at=FIXED_NOW,
    )

    exp_vars = [v for v in exp.variables if v.variable_class == VariableClass.EXPERIMENTAL]
    assert len(exp_vars) >= 2

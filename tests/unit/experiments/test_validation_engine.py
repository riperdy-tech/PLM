"""Tests for P3 Validation Engine (Task P3.8)."""

from __future__ import annotations

from datetime import UTC, datetime

from services.experiments.domain.enums import (
    ExperimentStatus,
    ExperimentType,
    VariableClass,
    VocalGender,
    VocalMode,
)
from services.experiments.domain.experiment import (
    Experiment,
    ExperimentVariable,
    Hypothesis,
    Treatment,
)
from services.experiments.domain.genome import BpmRange, ContentGenome, DurationRange
from services.experiments.validation.engine import (
    validate_experiment,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _sample_valid_experiment() -> tuple[Experiment, ContentGenome]:
    genome = ContentGenome(
        genre="rnb",
        subgenre="contemporary_rnb",
        mood=("melancholic", "dreamy"),
        use_case=("late_night_drive",),
        language="english",
        vocal_mode=VocalMode.VOCAL,
        vocal_gender=VocalGender.FEMALE,
        bpm=BpmRange(min_bpm=75, max_bpm=85, target_bpm=80),
        track_duration=DurationRange(min_seconds=160, max_seconds=220, target_seconds=190),
        playlist_duration=DurationRange(min_seconds=2700, max_seconds=3600, target_seconds=3000),
        visual_world="rainy_asian_city",
    )
    t1 = Treatment(
        treatment_id="t1",
        treatment_index=0,
        name="control",
        variable_assignments={"bpm": 80},
    )
    t2 = Treatment(
        treatment_id="t2",
        treatment_index=1,
        name="low_bpm",
        variable_assignments={"bpm": 70},
    )
    var = ExperimentVariable(
        name="bpm",
        variable_class=VariableClass.EXPERIMENTAL,
        baseline_value=80,
        treatment_values=(70,),
    )
    exp = Experiment(
        experiment_id="exp_val_01",
        content_arm_id="arm_01",
        production_directive_id="dir_01",
        experiment_type=ExperimentType.SINGLE_FACTOR,
        status=ExperimentStatus.DRAFT,
        hypothesis=Hypothesis(statement="Test", target_metric="ctr"),
        variables=(var,),
        treatments=(t1, t2),
        base_genome_fingerprint="fp_val",
        created_at=NOW,
    )
    return exp, genome


def test_valid_experiment_passes_validation() -> None:
    exp, genome = _sample_valid_experiment()
    report = validate_experiment(exp, genome, budget_limit=100.0)
    assert report.passed
    assert len(report.errors) == 0


def test_duplicate_treatments_detected() -> None:
    exp, genome = _sample_valid_experiment()
    dup_t2 = Treatment(
        treatment_id="t2_dup",
        treatment_index=1,
        name="dup",
        variable_assignments={"bpm": 80},  # identical to t1
    )
    dup_exp = exp.model_copy(update={"treatments": (exp.treatments[0], dup_t2)})

    report = validate_experiment(dup_exp, genome)
    assert not report.passed
    assert any("Duplicate" in err for err in report.errors)


def test_budget_ceiling_enforcement() -> None:
    exp, genome = _sample_valid_experiment()
    # If budget_limit is 50.0 and cost or ceiling is checked:
    report = validate_experiment(exp, genome, budget_limit=-5.0)
    assert not report.passed
    assert any("Budget" in err or "budget" in err for err in report.errors)


def test_uncontrolled_variable_mutation_detected() -> None:
    exp, genome = _sample_valid_experiment()
    # Treatment mutates visual_world which is not declared as an experimental variable
    bad_t2 = Treatment(
        treatment_id="t2_bad",
        treatment_index=1,
        name="uncontrolled",
        variable_assignments={"visual_world": "tokyo_cyberpunk"},
    )
    bad_exp = exp.model_copy(update={"treatments": (exp.treatments[0], bad_t2)})

    report = validate_experiment(bad_exp, genome)
    assert not report.passed
    assert any("undeclared" in err or "uncontrolled" in err for err in report.errors)

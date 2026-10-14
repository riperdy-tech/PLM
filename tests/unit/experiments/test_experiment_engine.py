"""Behavioural tests for P3 experiment design (P3 §12–§18, §36–§38, stage R2).

Each test names what it proves. They run the real design engine over a real C2 wire
directive; the stub they replace emitted ``variant_{idx}`` placeholders, moved two
variables together, repeated treatments and ignored the seed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts import ObservationWindow
from plm_contracts.seams.c2 import ProductionDirectivePayload

from services.experiments.domain.enums import (
    ExperimentStatus,
    ExperimentType,
    FactorDesign,
    VariableClass,
)
from services.experiments.domain.errors import ExperimentDesignError, ExperimentStateError
from services.experiments.domain.experiment import Experiment
from services.experiments.domain.genome import ContentGenome
from services.experiments.experiments.engine import design_experiment
from services.experiments.experiments.values import VALUE_GENERATORS
from services.experiments.genome.engine import (
    CreativeLatitudeEnvelope,
    build_genome_from_directive,
    default_template_library,
)
from services.experiments.ingestion.p2_adapter import AdaptedDirective, adapt_directive
from services.experiments.validation.engine import promote_to_validated, validate_experiment
from tests.support.seams import directive_fields

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _directive(**overrides: Any) -> AdaptedDirective:
    return adapt_directive(ProductionDirectivePayload.build(**directive_fields(**overrides)).to_wire())


def _setup(**overrides: Any) -> tuple[AdaptedDirective, ContentGenome]:
    directive = _directive(**overrides)
    return directive, build_genome_from_directive(directive)


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _differences(exp: Experiment) -> list[set[str]]:
    control = exp.treatments[0].variable_assignments
    return [
        {
            k
            for k in set(t.variable_assignments) | set(control)
            if _key(t.variable_assignments.get(k)) != _key(control.get(k))
        }
        for t in exp.treatments[1:]
    ]


def _experimentable(level: str) -> tuple[str, ...]:
    return CreativeLatitudeEnvelope.default_for_exploration(level).experimentable_variables


# --- Treatment values come from the ontology, never placeholders -------------------------


@pytest.mark.parametrize("variable", _experimentable("radical_explore"))
def test_every_permitted_variable_gets_real_ontology_values(variable: str) -> None:
    directive, genome = _setup(allocation_type="radical_explore", quantity=4)
    exp = design_experiment(directive, genome, target_variable=variable, seed=3, created_at=FIXED_NOW)

    alternatives = default_template_library().for_genre("rnb").alternatives
    for trt in exp.treatments[1:]:
        value = trt.genome_delta[variable]
        assert "variant_" not in json.dumps(value)
        if variable in alternatives:
            assert value in alternatives[variable]
        # The delta applies to a valid genome (enum, BPM range and vocal rules hold).
        ContentGenome.model_validate({**genome.model_dump(mode="json"), variable: value})


def test_bpm_values_stay_inside_the_creative_latitude() -> None:
    directive, genome = _setup(quantity=4)
    exp = design_experiment(directive, genome, target_variable="bpm", seed=1, created_at=FIXED_NOW)
    base = genome.bpm.target_bpm
    assert base is not None
    pct = CreativeLatitudeEnvelope.default_for_exploration("exploit").bpm_variance_pct
    for trt in exp.treatments[1:]:
        target = trt.genome_delta["bpm"]["target_bpm"]
        assert target != base
        assert abs(target - base) <= base * pct


# --- One factor at a time ----------------------------------------------------------------


@pytest.mark.parametrize("variable", ["bpm", "energy", "title_archetype", "thumbnail_family"])
def test_single_factor_treatments_differ_from_control_in_exactly_the_target(variable: str) -> None:
    directive, genome = _setup(quantity=3)
    exp = design_experiment(directive, genome, target_variable=variable, seed=5, created_at=FIXED_NOW)
    assert exp.experiment_type is ExperimentType.SINGLE_FACTOR
    assert exp.factor_design is FactorDesign.ONE_FACTOR_PER_TREATMENT
    assert _differences(exp) == [{variable}] * (len(exp.treatments) - 1)
    for trt in exp.treatments[1:]:
        assert set(trt.genome_delta) == {variable}
    experimental = [v.name for v in exp.variables if v.variable_class is VariableClass.EXPERIMENTAL]
    assert experimental == [variable]


@pytest.mark.parametrize("variable", ["bpm", "title_archetype", "thumbnail_family"])
def test_four_requested_treatments_are_pairwise_distinct(variable: str) -> None:
    directive, genome = _setup(quantity=4)
    exp = design_experiment(directive, genome, target_variable=variable, seed=9, created_at=FIXED_NOW)
    assert len(exp.treatments) == 4
    keys = [_key(t.variable_assignments) for t in exp.treatments]
    assert len(set(keys)) == 4


def test_fewer_distinct_values_than_requested_yields_fewer_treatments_with_a_reason() -> None:
    # Exploit energy latitude is one step either side of "medium": two alternatives only.
    directive, genome = _setup(quantity=6)
    exp = design_experiment(directive, genome, target_variable="energy", seed=0, created_at=FIXED_NOW)
    assert [t.genome_delta["energy"] for t in exp.treatments[1:]] == ["high", "low"]
    assert "treatments_reduced:requested=5,distinct=2" in exp.design_notes
    assert validate_experiment(exp, genome).passed


# --- Refusals instead of placeholders ----------------------------------------------------


@pytest.mark.parametrize(
    ("variable", "level", "code"),
    [
        ("mood_lighting", "exploit", "no_value_generator"),
        ("genre", "radical_explore", "variable_is_hard_constraint"),
        ("track_duration", "radical_explore", "variable_is_hard_constraint"),
        ("visual_world", "exploit", "variable_outside_creative_latitude"),
        ("song_structure", "adjacent_explore", "variable_outside_creative_latitude"),
    ],
)
def test_variables_without_a_valid_generator_are_refused(variable: str, level: str, code: str) -> None:
    directive, genome = _setup(allocation_type=level)
    with pytest.raises(ExperimentDesignError) as exc:
        design_experiment(directive, genome, target_variable=variable, created_at=FIXED_NOW)
    assert exc.value.code == code


def test_variable_with_no_alternative_in_the_genre_ontology_is_refused() -> None:
    # The lofi template declares no lyric_density alternatives (instrumental genre).
    directive, genome = _setup(
        allocation_type="radical_explore",
        brief={"genre": "lofi", "language": "english", "vocal_mode": "instrumental"},
    )
    with pytest.raises(ExperimentDesignError) as exc:
        design_experiment(directive, genome, target_variable="lyric_density", created_at=FIXED_NOW)
    assert exc.value.code == "no_candidate_values"


def test_registry_covers_exactly_the_generated_dimensions() -> None:
    for level in ("exploit", "adjacent_explore", "radical_explore"):
        assert set(_experimentable(level)) <= set(VALUE_GENERATORS)


# --- Multi-factor is an explicit design --------------------------------------------------


def test_multi_factor_without_declared_factors_is_refused() -> None:
    directive, genome = _setup(allocation_type="radical_explore", quantity=5)
    with pytest.raises(ExperimentDesignError) as exc:
        design_experiment(
            directive, genome, experiment_type=ExperimentType.MULTI_FACTOR, created_at=FIXED_NOW
        )
    assert exc.value.code == "multi_factor_requires_factors"


def test_radical_explore_alone_does_not_make_factors_move_together() -> None:
    directive, genome = _setup(allocation_type="radical_explore", quantity=4)
    exp = design_experiment(directive, genome, seed=2, created_at=FIXED_NOW)
    assert exp.experiment_type is ExperimentType.SINGLE_FACTOR
    assert all(len(diff) == 1 for diff in _differences(exp))


def test_multi_factor_one_factor_per_treatment() -> None:
    directive, genome = _setup(allocation_type="radical_explore", quantity=5)
    exp = design_experiment(
        directive,
        genome,
        experiment_type=ExperimentType.MULTI_FACTOR,
        factors=("bpm", "visual_world"),
        seed=999,
        created_at=FIXED_NOW,
    )
    assert exp.factor_design is FactorDesign.ONE_FACTOR_PER_TREATMENT
    diffs = _differences(exp)
    assert all(len(diff) == 1 for diff in diffs)
    assert {name for diff in diffs for name in diff} == {"bpm", "visual_world"}
    exp_vars = [v for v in exp.variables if v.variable_class is VariableClass.EXPERIMENTAL]
    assert {v.name for v in exp_vars} == {"bpm", "visual_world"}
    assert validate_experiment(exp, genome).passed


def test_multi_factor_needs_a_slot_per_factor() -> None:
    directive, genome = _setup(allocation_type="radical_explore", quantity=2)
    with pytest.raises(ExperimentDesignError) as exc:
        design_experiment(
            directive,
            genome,
            experiment_type=ExperimentType.MULTI_FACTOR,
            factors=("bpm", "visual_world"),
            created_at=FIXED_NOW,
        )
    assert exc.value.code == "multi_factor_needs_slot_per_factor"


def test_full_factorial_is_stated_and_covers_every_cell() -> None:
    directive, genome = _setup(allocation_type="radical_explore", quantity=4)
    exp = design_experiment(
        directive,
        genome,
        experiment_type=ExperimentType.MULTI_FACTOR,
        factors=("bpm", "visual_world"),
        factor_design=FactorDesign.FULL_FACTORIAL,
        seed=4,
        created_at=FIXED_NOW,
    )
    assert exp.factor_design is FactorDesign.FULL_FACTORIAL
    assert sorted(len(d) for d in _differences(exp)) == [1, 1, 2]
    assert validate_experiment(exp, genome).passed


def test_full_factorial_with_too_few_slots_is_refused() -> None:
    directive, genome = _setup(allocation_type="radical_explore", quantity=3)
    with pytest.raises(ExperimentDesignError) as exc:
        design_experiment(
            directive,
            genome,
            experiment_type=ExperimentType.MULTI_FACTOR,
            factors=("bpm", "visual_world"),
            factor_design=FactorDesign.FULL_FACTORIAL,
            created_at=FIXED_NOW,
        )
    assert exc.value.code == "factorial_needs_more_slots"


# --- Determinism and the seed ------------------------------------------------------------


def test_experiment_design_is_deterministic() -> None:
    directive, genome = _setup(quantity=4)
    exp1 = design_experiment(directive, genome, target_variable="title_archetype", seed=123, created_at=FIXED_NOW)
    exp2 = design_experiment(directive, genome, target_variable="title_archetype", seed=123, created_at=FIXED_NOW)
    assert exp1 == exp2


def test_seed_is_honoured_and_recorded() -> None:
    directive, genome = _setup(quantity=4)
    orders = {
        tuple(
            t.genome_delta["thumbnail_family"]
            for t in design_experiment(
                directive, genome, target_variable="thumbnail_family", seed=seed, created_at=FIXED_NOW
            ).treatments[1:]
        )
        for seed in range(8)
    }
    assert len(orders) > 1
    exp = design_experiment(directive, genome, target_variable="thumbnail_family", seed=17, created_at=FIXED_NOW)
    assert exp.randomization.seed == 17
    other = design_experiment(directive, genome, target_variable="thumbnail_family", seed=18, created_at=FIXED_NOW)
    assert other.experiment_id != exp.experiment_id


# --- Status, measurement plan and budget -------------------------------------------------


def test_design_is_draft_until_the_validator_promotes_it() -> None:
    directive, genome = _setup()
    exp = design_experiment(directive, genome, seed=42, created_at=FIXED_NOW)
    assert exp.status is ExperimentStatus.DRAFT
    report = validate_experiment(exp, genome)
    validated = promote_to_validated(exp, report)
    assert validated.status is ExperimentStatus.VALIDATED
    assert exp.status is ExperimentStatus.DRAFT  # immutable: a new value is returned


def test_failed_or_foreign_report_never_promotes() -> None:
    directive, genome = _setup()
    exp = design_experiment(directive, genome, seed=42, created_at=FIXED_NOW)
    failing = validate_experiment(exp.model_copy(update={"budget_limit": 0.0}), genome)
    assert not failing.passed
    with pytest.raises(ExperimentStateError) as exc:
        promote_to_validated(exp, failing)
    assert exc.value.code == "validation_failed"

    other = design_experiment(directive, genome, seed=43, created_at=FIXED_NOW)
    with pytest.raises(ExperimentStateError) as exc:
        promote_to_validated(exp, validate_experiment(other, genome))
    assert exc.value.code == "validation_report_mismatch"


def test_measurement_plan_records_the_directives_horizon() -> None:
    directive, genome = _setup(outcome_horizon="7d")
    exp = design_experiment(directive, genome, created_at=FIXED_NOW)
    assert exp.measurement_plan.outcome_horizon is ObservationWindow.D7
    assert exp.measurement_plan.experiment_id == exp.experiment_id
    assert exp.measurement_plan.maturity_rule.value == "publication_plus_horizon"


def test_directive_budget_limit_is_the_experiment_ceiling() -> None:
    directive, genome = _setup(budget_limit=42.5)
    exp = design_experiment(directive, genome, created_at=FIXED_NOW)
    assert exp.budget_limit == 42.5

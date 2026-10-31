"""Manual supply (D10): the owner makes every song by hand, so P3 asks for one package
with one candidate per track instead of control + treatment with two candidates each."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from plm_contracts.seams.c2 import ProductionDirectivePayload

from services.experiments.config import load_experiments_settings
from services.experiments.experiments.engine import design_experiment
from services.experiments.genome.engine import build_genome_from_directive
from services.experiments.ingestion.p2_adapter import adapt_directive
from services.experiments.materialization.builder import materialize_package
from services.experiments.validation.engine import promote_to_validated, validate_experiment
from tests.support.seams import directive_fields

FIXED_NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _setup(**overrides: Any) -> tuple[Any, Any]:
    directive = adapt_directive(
        ProductionDirectivePayload.build(**directive_fields(**overrides)).to_wire())
    return directive, build_genome_from_directive(directive)


def test_settings_default_to_automated_supply() -> None:
    assert load_experiments_settings().manual_supply is False
    assert load_experiments_settings(manual_supply=True).manual_supply is True


def test_control_only_design_validates_with_the_control_alone() -> None:
    directive, genome = _setup()
    exp = design_experiment(directive, genome, seed=7, created_at=FIXED_NOW, control_only=True)

    assert [t.name for t in exp.treatments] == ["control"]
    assert "control_only:manual_supply" in exp.design_notes
    report = validate_experiment(exp, genome)
    assert report.passed, report
    assert promote_to_validated(exp, report).status.value == "validated"


def test_default_design_still_adds_a_treatment() -> None:
    directive, genome = _setup()
    exp = design_experiment(directive, genome, seed=7, created_at=FIXED_NOW)
    assert len(exp.treatments) >= 2


def test_manual_supply_package_asks_one_candidate_per_track() -> None:
    directive, genome = _setup()
    exp = design_experiment(directive, genome, seed=7, created_at=FIXED_NOW, control_only=True)
    validated = promote_to_validated(exp, validate_experiment(exp, genome))

    manual = materialize_package(validated, validated.treatments[0], genome,
                                 candidate_target=1, created_at=FIXED_NOW)
    default = materialize_package(validated, validated.treatments[0], genome,
                                  created_at=FIXED_NOW)

    assert {spec.candidate_target for spec in manual.track_specs} == {1}
    assert {spec.candidate_target for spec in default.track_specs} == {2}
    assert len(manual.track_specs) == len(default.track_specs)


def test_manual_supply_package_widens_the_per_song_range_only() -> None:
    from plm_contracts.seams.c3 import DurationRangeSeconds

    from services.experiments.config import MANUAL_TRACK_SECONDS

    directive, genome = _setup()
    exp = design_experiment(directive, genome, seed=7, created_at=FIXED_NOW, control_only=True)
    validated = promote_to_validated(exp, validate_experiment(exp, genome))
    wide = DurationRangeSeconds(min_seconds=MANUAL_TRACK_SECONDS[0],
                                max_seconds=MANUAL_TRACK_SECONDS[1])

    manual = materialize_package(validated, validated.treatments[0], genome,
                                 track_duration_range=wide, created_at=FIXED_NOW)
    default = materialize_package(validated, validated.treatments[0], genome,
                                  created_at=FIXED_NOW)

    assert {spec.duration_range for spec in manual.track_specs} == {wide}
    assert {spec.duration_range for spec in default.track_specs} != {wide}
    # The plan is unchanged: same requested length, same track count.
    assert [s.requested_duration for s in manual.track_specs] == [
        s.requested_duration for s in default.track_specs]

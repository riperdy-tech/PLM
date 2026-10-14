"""Tests for P3 materializer builder (Task P3.7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.experiments.domain.enums import PackageStatus, TrackRole
from services.experiments.domain.experiment import (
    Experiment,
    Hypothesis,
    Treatment,
)
from services.experiments.domain.genome import BpmRange, ContentGenome, DurationRange
from services.experiments.materialization.builder import (
    BudgetEnvelopeExceededError,
    materialize_package,
)

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _sample_experiment() -> tuple[Experiment, Treatment, ContentGenome]:
    genome = ContentGenome(
        genre="rnb",
        subgenre="contemporary_rnb",
        mood=("melancholic", "dreamy"),
        use_case=("late_night_drive",),
        language="english",
        bpm=BpmRange(min_bpm=75, max_bpm=85, target_bpm=80),
        track_duration=DurationRange(min_seconds=160, max_seconds=220, target_seconds=190),
        playlist_duration=DurationRange(min_seconds=2700, max_seconds=3600, target_seconds=3000),
        visual_world="rainy_asian_city",
    )
    trt = Treatment(
        treatment_id="trt_mat_01",
        treatment_index=0,
        name="control",
        variable_assignments={"bpm": 80},
    )
    exp = Experiment(
        experiment_id="exp_mat_01",
        content_arm_id="arm_rnb_01",
        production_directive_id="dir_01",
        experiment_type="single_factor",  # type: ignore[arg-type]
        status="validated",  # type: ignore[arg-type]
        hypothesis=Hypothesis(statement="Statement", target_metric="completion"),
        treatments=(trt,),
        base_genome_fingerprint="fp_dummy",
        created_at=NOW,
    )
    return exp, trt, genome


def test_materialize_package_creates_all_specs() -> None:
    exp, trt, genome = _sample_experiment()
    pkg = materialize_package(
        exp,
        trt,
        genome,
        brand_id="brand_lofi",
        budget_envelope=150.0,
        track_count=3,
        total_duration_minutes=45,
        created_at=FIXED_NOW,
    )

    assert pkg.status == PackageStatus.MATERIALIZED
    assert pkg.brand_id == "brand_lofi"
    assert pkg.budget_envelope == 150.0
    assert len(pkg.track_specs) == 3
    assert pkg.track_specs[0].track_role == TrackRole.FOCUS
    assert pkg.track_specs[1].track_role == TrackRole.SECONDARY
    assert pkg.track_specs[0].budget_limit == 50.0
    assert pkg.playlist_spec.total_duration_minutes == 45
    assert pkg.visual_spec.visual_world == "rainy_asian_city"
    assert pkg.thumbnail_spec.thumbnail_family == "illustrated_lofi"
    assert pkg.title_spec.title_archetype == "evocative_phrase"
    assert len(pkg.package_hash) == 64


def test_budget_envelope_must_be_positive() -> None:
    exp, trt, genome = _sample_experiment()
    with pytest.raises(BudgetEnvelopeExceededError):
        materialize_package(exp, trt, genome, budget_envelope=-10.0, created_at=FIXED_NOW)

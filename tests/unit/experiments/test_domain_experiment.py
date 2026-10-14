"""Tests for P3 experiment domain model, fingerprinting, and immutability (P3.2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from services.experiments.domain.enums import (
    ExperimentStatus,
    ExperimentType,
    PackageStatus,
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
from services.experiments.domain.fingerprint import (
    compute_experiment_fingerprint,
    compute_genome_fingerprint,
    compute_package_hash,
)
from services.experiments.domain.genome import BpmRange, ContentGenome, DurationRange
from services.experiments.domain.package import (
    ContentPackage,
    PlaylistSpec,
    ThumbnailSpec,
    TitleSpec,
    TrackSpec,
    VisualSpec,
)
from tests.support.experiments_factories import design_facts
from tests.support.seams import lineage_fields, track_spec_fields


def _sample_genome() -> ContentGenome:
    return ContentGenome(
        genre="rnb",
        subgenre="contemporary_rnb",
        mood=("melancholic", "intimate"),
        use_case=("late_night_drive",),
        language="english",
        vocal_mode=VocalMode.VOCAL,
        vocal_gender=VocalGender.FEMALE,
        bpm=BpmRange(min_bpm=75, max_bpm=85, target_bpm=80),
        track_duration=DurationRange(min_seconds=160, max_seconds=220, target_seconds=190),
        playlist_duration=DurationRange(min_seconds=2700, max_seconds=3600, target_seconds=3000),
        visual_world="rainy_asian_city",
    )


def test_genome_fingerprint_is_deterministic() -> None:
    g1 = _sample_genome()
    g2 = _sample_genome()

    fp1 = compute_genome_fingerprint(g1)
    fp2 = compute_genome_fingerprint(g2)
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA256 hex


def test_genome_fingerprint_changes_on_dimension_delta() -> None:
    g1 = _sample_genome()
    g2 = ContentGenome(**{**g1.model_dump(), "visual_world": "tokyo_cyberpunk"})

    assert compute_genome_fingerprint(g1) != compute_genome_fingerprint(g2)


def test_experiment_definition_and_immutability() -> None:
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    hyp = Hypothesis(
        statement="Slower BPM increases completion rate in late-night chill use cases.",
        target_metric="completion_rate",
        expected_direction="increase",
        rationale="Late-night driving listeners favor lower arousal rhythms.",
    )
    var = ExperimentVariable(
        name="bpm",
        variable_class=VariableClass.EXPERIMENTAL,
        baseline_value={"min": 80, "max": 90},
        treatment_values=({"min": 70, "max": 80}, {"min": 90, "max": 100}),
    )
    treatments = (
        Treatment(
            treatment_id="trt_control",
            treatment_index=0,
            name="control",
            variable_assignments={"bpm": {"min": 80, "max": 90}},
        ),
        Treatment(
            treatment_id="trt_low_bpm",
            treatment_index=1,
            name="low_bpm",
            variable_assignments={"bpm": {"min": 70, "max": 80}},
        ),
    )
    exp = Experiment(
        experiment_id="exp_01",
        content_arm_id="arm_rnb_01",
        production_directive_id="dir_01",
        experiment_type=ExperimentType.SINGLE_FACTOR,
        status=ExperimentStatus.DRAFT,
        hypothesis=hyp,
        variables=(var,),
        treatments=treatments,
        base_genome_fingerprint=compute_genome_fingerprint(_sample_genome()),
        experiment_version="p3-exp-v1",
        created_at=now,
        **design_facts("exp_01"),
    )

    assert exp.experiment_id == "exp_01"
    assert len(exp.treatments) == 2

    # Immutability
    with pytest.raises((TypeError, ValidationError)):
        exp.status = ExperimentStatus.COMPLETED  # type: ignore[misc]

    # The measurement plan and budget ceiling are required facts, never defaulted.
    facts = design_facts("exp_01")
    for missing in ("measurement_plan", "budget_limit", "randomization"):
        partial = {k: v for k, v in facts.items() if k != missing}
        with pytest.raises(ValidationError):
            Experiment(**{**exp.model_dump(exclude=set(facts)), **partial})
    # A plan designed for another experiment cannot be attached.
    with pytest.raises(ValidationError, match="does not match"):
        Experiment(**{**exp.model_dump(exclude=set(facts)), **design_facts("exp_other")})


def test_experiment_fingerprint_computation() -> None:
    var1 = ExperimentVariable(
        name="bpm",
        variable_class=VariableClass.EXPERIMENTAL,
        baseline_value=80,
        treatment_values=(70, 90),
    )
    fp1 = compute_experiment_fingerprint("exp_01", "arm_rnb_01", (var1,))
    fp2 = compute_experiment_fingerprint("exp_01", "arm_rnb_01", (var1,))
    assert fp1 == fp2
    assert len(fp1) == 64

    fp3 = compute_experiment_fingerprint("exp_01", "arm_rnb_02", (var1,))
    assert fp1 != fp3


def test_package_hash_and_model() -> None:
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    track = TrackSpec(
        **track_spec_fields(lineage=lineage_fields(experiment_id="exp_01"))
    )
    ply = PlaylistSpec(
        playlist_spec_id="ply_01",
        experiment_id="exp_01",
        brand_id="brand_lofi",
        track_count_target=10,
        total_duration_minutes=60,
        sequencing_profile="progressive_arc",
    )
    vis = VisualSpec(
        visual_spec_id="vis_01",
        experiment_id="exp_01",
        visual_world="rainy_asian_city",
        motion_profile="cinematic_drift",
        palette_guide=("deep_blue", "neon_magenta"),
        aspect_ratio="16:9",
    )
    thb = ThumbnailSpec(
        thumbnail_spec_id="thb_01",
        experiment_id="exp_01",
        thumbnail_family="illustrated_lofi",
        composition_archetype="lone_figure_in_car",
        text_rules={"max_words": 3},
    )
    ttl = TitleSpec(
        title_spec_id="ttl_01",
        experiment_id="exp_01",
        title_archetype="evocative_phrase",
        formatting_pattern="{mood} {genre} for {use_case}",
        character_limit=60,
    )

    pkg_hash = compute_package_hash("exp_01", "trt_control", (track.track_spec_id,))
    pkg = ContentPackage(
        package_id="pkg_01",
        experiment_id="exp_01",
        treatment_id="trt_control",
        brand_id="brand_lofi",
        status=PackageStatus.MATERIALIZED,
        track_specs=(track,),
        playlist_spec=ply,
        visual_spec=vis,
        thumbnail_spec=thb,
        title_spec=ttl,
        package_hash=pkg_hash,
        budget_envelope=250.0,
        created_at=now,
    )

    assert pkg.package_id == "pkg_01"
    assert len(pkg.track_specs) == 1
    assert pkg.package_hash == pkg_hash

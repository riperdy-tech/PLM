"""Tests for P3 repositories on SQLite harness (Task P3.3)."""

from __future__ import annotations

from datetime import UTC, datetime

from services.experiments.domain.enums import (
    ConstraintType,
    ExperimentStatus,
    ExperimentType,
    PackageStatus,
    TrackRole,
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
    compute_genome_fingerprint,
    compute_package_hash,
)
from services.experiments.domain.genome import (
    BpmRange,
    ConstraintEnvelope,
    ContentGenome,
    DurationRange,
)
from services.experiments.domain.package import (
    ContentPackage,
    PlaylistSpec,
    ThumbnailSpec,
    TitleSpec,
    TrackSpec,
    VisualSpec,
)
from services.experiments.storage import repositories
from tests.support.experiments_db import create_experiments_engine, experiments_session
from tests.support.experiments_factories import design_facts, playlist_spec_fields
from tests.support.seams import lineage_fields, track_spec_fields

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


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


def test_genome_persistence_and_idempotency() -> None:
    engine = create_experiments_engine()
    with experiments_session(engine) as session:
        genome = _sample_genome()
        env = ConstraintEnvelope(overrides={"bpm": ConstraintType.HARD})

        gid1 = repositories.insert_genome(
            session, genome, genome_id="gen_01", envelope=env, created_at=NOW
        )
        session.commit()
        assert gid1 == "gen_01"

        # Re-inserting identical genome returns the existing id
        gid2 = repositories.insert_genome(
            session, genome, genome_id="gen_02", envelope=env, created_at=NOW
        )
        assert gid2 == "gen_01"

        # Retrieve by fingerprint
        fp = compute_genome_fingerprint(genome)
        loaded = repositories.get_genome_by_fingerprint(session, fp)
        assert loaded is not None
        assert loaded.genre == "rnb"
        assert loaded.bpm.target_bpm == 80


def test_experiment_persistence_and_status_update() -> None:
    engine = create_experiments_engine()
    with experiments_session(engine) as session:
        hyp = Hypothesis(
            statement="Lower BPM increases retention",
            target_metric="retention_rate",
            expected_direction="increase",
        )
        var = ExperimentVariable(
            name="bpm",
            variable_class=VariableClass.EXPERIMENTAL,
            baseline_value=80,
            treatment_values=(70, 90),
        )
        trt = Treatment(
            treatment_id="trt_low",
            treatment_index=0,
            name="low_bpm",
            variable_assignments={"bpm": 70},
        )
        exp = Experiment(
            experiment_id="exp_test_01",
            content_arm_id="arm_01",
            production_directive_id="dir_01",
            experiment_type=ExperimentType.SINGLE_FACTOR,
            status=ExperimentStatus.DRAFT,
            hypothesis=hyp,
            variables=(var,),
            treatments=(trt,),
            base_genome_fingerprint=compute_genome_fingerprint(_sample_genome()),
            created_at=NOW,
            **design_facts("exp_test_01"),
        )

        repositories.insert_experiment(session, exp)
        session.commit()

        loaded = repositories.get_experiment(session, "exp_test_01")
        assert loaded is not None
        assert loaded.content_arm_id == "arm_01"
        assert loaded.status == ExperimentStatus.DRAFT
        assert len(loaded.treatments) == 1
        assert loaded.treatments[0].variable_assignments["bpm"] == 70

        # Status transition
        repositories.update_experiment_status(
            session, "exp_test_01", ExperimentStatus.MATERIALIZED, now=FIXED_NOW
        )
        session.commit()

        updated = repositories.get_experiment(session, "exp_test_01")
        assert updated is not None
        assert updated.status == ExperimentStatus.MATERIALIZED


def test_content_package_persistence_and_retrieval() -> None:
    engine = create_experiments_engine()
    with experiments_session(engine) as session:
        # First create experiment for foreign key
        hyp = Hypothesis(statement="Hyp", target_metric="ctr")
        exp = Experiment(
            experiment_id="exp_test_02",
            content_arm_id="arm_01",
            production_directive_id="dir_01",
            experiment_type=ExperimentType.SINGLE_FACTOR,
            status=ExperimentStatus.MATERIALIZED,
            hypothesis=hyp,
            base_genome_fingerprint=compute_genome_fingerprint(_sample_genome()),
            created_at=NOW,
            **design_facts("exp_test_02"),
        )
        repositories.insert_experiment(session, exp)

        track = TrackSpec(
            **track_spec_fields(lineage=lineage_fields(experiment_id="exp_test_02"))
        )
        ply = PlaylistSpec(**playlist_spec_fields(
            experiment_id="exp_test_02", base_track_count=6, spare_track_count=2,
            sequencing_profile="progressive",
        ))
        vis = VisualSpec(
            visual_spec_id="vis_01",
            experiment_id="exp_test_02",
            visual_world="neon_seoul",
            motion_profile="slow_pan",
            palette_guide=("blue", "violet"),
        )
        thb = ThumbnailSpec(
            thumbnail_spec_id="thb_01",
            experiment_id="exp_test_02",
            thumbnail_family="lofi_art",
            composition_archetype="close_up",
        )
        ttl = TitleSpec(
            title_spec_id="ttl_01",
            experiment_id="exp_test_02",
            title_archetype="evocative",
            formatting_pattern="{mood}",
            character_limit=80,
        )

        pkg_hash = compute_package_hash("exp_test_02", "trt_01", ("trk_01",))
        pkg = ContentPackage(
            package_id="pkg_test_01",
            experiment_id="exp_test_02",
            treatment_id="trt_01",
            brand_id="brand_lofi",
            status=PackageStatus.MATERIALIZED,
            track_specs=(track,),
            playlist_spec=ply,
            visual_spec=vis,
            thumbnail_spec=thb,
            title_spec=ttl,
            package_hash=pkg_hash,
            budget_envelope=150.0,
            created_at=NOW,
        )

        repositories.insert_content_package(session, pkg)
        session.commit()

        loaded_pkg = repositories.get_content_package(session, "pkg_test_01")
        assert loaded_pkg is not None
        assert loaded_pkg.package_id == "pkg_test_01"
        assert len(loaded_pkg.track_specs) == 1
        assert loaded_pkg.track_specs[0].track_role == TrackRole.FOCUS
        assert loaded_pkg.playlist_spec.track_count_target == 8
        assert loaded_pkg.playlist_spec == ply  # the declared range round-trips
        assert loaded_pkg.visual_spec.visual_world == "neon_seoul"


def test_audit_event_recording() -> None:
    engine = create_experiments_engine()
    with experiments_session(engine) as session:
        repositories.record_event(
            session,
            event_id="evt_01",
            experiment_id="exp_01",
            event_type="experiment_created",
            payload={"created_by": "test_agent"},
            created_at=NOW,
        )
        session.commit()


def test_measurement_plan_budget_and_design_round_trip() -> None:
    from plm_contracts.seams.c2 import ProductionDirectivePayload

    from services.experiments.experiments.engine import design_experiment
    from services.experiments.genome.engine import build_genome_from_directive
    from services.experiments.ingestion.p2_adapter import adapt_directive
    from tests.support.seams import directive_fields

    directive = adapt_directive(
        ProductionDirectivePayload.build(
            **directive_fields(outcome_horizon="7d", budget_limit=55.0, quantity=6)
        ).to_wire()
    )
    genome = build_genome_from_directive(directive)
    exp = design_experiment(directive, genome, target_variable="energy", seed=3, created_at=NOW)
    assert exp.design_notes  # energy has fewer alternatives than requested
    engine = create_experiments_engine()
    with experiments_session(engine) as session:
        repositories.insert_experiment(session, exp)
        session.commit()
        loaded = repositories.get_experiment(session, exp.experiment_id)
    assert loaded is not None
    assert loaded.measurement_plan == exp.measurement_plan
    assert loaded.measurement_plan.outcome_horizon.value == "7d"
    assert loaded.budget_limit == 55.0
    assert loaded.randomization == exp.randomization
    assert loaded.factor_design is exp.factor_design
    assert loaded.design_notes == exp.design_notes


def test_legacy_row_without_a_measurement_plan_is_refused_on_load() -> None:
    import pytest
    import sqlalchemy as sa

    from services.experiments.domain.errors import LegacyExperimentError
    from services.experiments.storage.models import experiments_table

    engine = create_experiments_engine()
    with experiments_session(engine) as session:
        session.execute(
            sa.insert(experiments_table).values(
                experiment_id="exp_legacy",
                content_arm_id="arm_01",
                production_directive_id="dir_01",
                experiment_type="single_factor",
                status="validated",
                hypothesis_statement="old",
                target_metric="retention_30s",
                expected_direction="increase",
                rationale="",
                base_genome_fingerprint="fp",
                experiment_version="p3-exp-v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
        with pytest.raises(LegacyExperimentError) as exc:
            repositories.get_experiment(session, "exp_legacy")
    assert exc.value.code == "legacy_experiment_without_measurement_plan"

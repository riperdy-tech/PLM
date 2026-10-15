"""Tests for P3 CLI commands and observability (Task P3.11)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from plm_contracts.seams.c2 import ProductionDirectivePayload
from plm_platform.cli import app
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from services.experiments.cli import (
    list_experiments,
    materialize_treatment_cli,
    show_experiment,
    validate_experiment_cli,
)
from services.experiments.domain.enums import ExperimentType
from services.experiments.domain.errors import ExperimentStateError
from services.experiments.experiments.engine import design_experiment
from services.experiments.genome.engine import build_genome_from_directive
from services.experiments.ingestion.p2_adapter import adapt_directive
from services.experiments.observability.logging import StructuredEvent, emit_event
from services.experiments.observability.metrics import (
    EXPERIMENTS_CREATED,
    EXPERIMENTS_MATERIALIZED,
    EXPERIMENTS_VALIDATED,
    record_experiment_created,
    record_experiment_validated,
    record_package_materialized,
)
from services.experiments.storage import repositories
from services.experiments.storage.ids import deterministic_id
from tests.support.experiments_db import create_experiments_engine

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

runner = CliRunner()

SAMPLE_DIRECTIVE = ProductionDirectivePayload.build(
    directive_id="dir_cli_sample_01", decision_id="dec_cli_01", arm_id="rnb_late_night_drive_english_vocal_45_75m", allocation_type="exploit", priority=1, quantity=1, brief={
        "genre": "rnb",
        "mood": ["melancholic", "intimate"],
        "use_case": ["late_night_drive"],
        "language": "english",
        "vocal_mode": "vocal",
        "duration_bucket": "45_75m",
        "visual_world": "rainy_asian_city",
    }, expected_value=85.0, uncertainty=0.15, budget_limit=100.0, market_context={"brand_id": "neon_seoul"}, selection_reason=["high_demand"], directive_version="p2-dir-v1", policy_version="p2-v1", expires_at="2026-10-01T00:00:00Z", exploration_fraction=0.2, value_model="early-stage-proxy-v1", outcome_horizon="28d"
).to_wire()


def _setup_seeded_db() -> tuple[sessionmaker, str, str]:
    engine = create_experiments_engine(static_pool=True)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        adapted = adapt_directive(SAMPLE_DIRECTIVE)
        genome = build_genome_from_directive(adapted)
        gid = deterministic_id("gen", adapted.arm_id, adapted.directive_id)
        repositories.insert_genome(session, genome, genome_id=gid, created_at=FIXED_NOW)

        exp = design_experiment(adapted, genome, experiment_type=ExperimentType.SINGLE_FACTOR, seed=42, created_at=FIXED_NOW)
        repositories.insert_experiment(session, exp)
        session.commit()
        return factory, exp.experiment_id, exp.treatments[0].treatment_id


def test_cli_list_and_show() -> None:
    factory, exp_id, _ = _setup_seeded_db()

    # List
    output_list = list_experiments(session_factory=factory)
    assert exp_id in output_list
    assert "single_factor" in output_list

    # Show
    output_show = show_experiment(exp_id, session_factory=factory)
    assert exp_id in output_show
    assert "hypothesis:" in output_show
    assert "treatments (2):" in output_show

    # Show nonexistent raises
    with pytest.raises(LookupError, match="does not exist"):
        show_experiment("exp_nonexistent", session_factory=factory)


def test_cli_validate() -> None:
    factory, exp_id, _ = _setup_seeded_db()

    val_out = validate_experiment_cli(exp_id, session_factory=factory)
    assert "Validation PASSED" in val_out
    assert "passed: True" in val_out

    with pytest.raises(LookupError):
        validate_experiment_cli("exp_missing", session_factory=factory)


def test_cli_materialize() -> None:
    factory, exp_id, trt_id = _setup_seeded_db()

    with pytest.raises(ExperimentStateError):  # draft: not validated yet
        materialize_treatment_cli(exp_id, trt_id, session_factory=factory)
    assert "status: validated" in validate_experiment_cli(exp_id, session_factory=factory)

    mat_out = materialize_treatment_cli(
        exp_id,
        trt_id,
        brand_id="neon_seoul",
        budget_envelope=75.0,
        session_factory=factory,
    )
    assert "Materialized package pkg_" in mat_out
    # rnb tracks plan 190 s; 45-75 min needs ceil(2700/190) = 15 plus 25 % spares = 4.
    assert "tracks: 19" in mat_out
    assert "playlist range: 2700-4500s" in mat_out
    assert "neon_seoul" in mat_out

    # Verify status changed in storage
    with factory() as session:
        exp = repositories.get_experiment(session, exp_id)
        assert exp is not None
        assert exp.status.value == "materialized"


def test_observability_metrics_and_events() -> None:
    record_experiment_created(experiment_type="single_factor", brand_id="neon_seoul")
    assert EXPERIMENTS_CREATED.labels(experiment_type="single_factor", brand_id="neon_seoul")._value.get() >= 1

    record_experiment_validated(passed=True)
    assert EXPERIMENTS_VALIDATED.labels(passed="true")._value.get() >= 1

    record_package_materialized(brand_id="neon_seoul")
    assert EXPERIMENTS_MATERIALIZED.labels(brand_id="neon_seoul")._value.get() >= 1

    # Structured audit events
    factory, exp_id, _ = _setup_seeded_db()
    with factory() as session:
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        evt = StructuredEvent(
            experiment_id=exp_id,
            event="test_event",
            state_before="validated",
            state_after="materialized",
        )
        evt_id = emit_event(session, evt, now=now)
        session.commit()

        assert evt_id.startswith("evt_")


def test_typer_app_experiments_help() -> None:
    result = runner.invoke(app, ["experiments", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "show" in result.output
    assert "validate" in result.output
    assert "materialize" in result.output
    assert "serve" in result.output
    assert "smoke" in result.output

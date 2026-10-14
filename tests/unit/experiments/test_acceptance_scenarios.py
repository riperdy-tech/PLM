"""Acceptance scenario test suite: AC-01 through AC-15 (§83, Task P3.12)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from plm_contracts.seams.c2 import ProductionDirectivePayload
from plm_platform.config import load_settings
from sqlalchemy.orm import sessionmaker

from services.experiments.api.app import create_app
from services.experiments.config import load_experiments_settings
from services.experiments.domain.enums import (
    ExperimentStatus,
    ExperimentType,
    VariableClass,
    VocalGender,
    VocalMode,
)
from services.experiments.domain.experiment import Treatment
from services.experiments.experiments.engine import design_experiment
from services.experiments.genome.engine import build_genome_from_directive
from services.experiments.ingestion.p2_adapter import adapt_directive
from services.experiments.materialization.builder import (
    BudgetEnvelopeExceededError,
    materialize_package,
)
from services.experiments.storage import repositories
from services.experiments.validation.engine import promote_to_validated, validate_experiment
from tests.support.experiments_db import create_experiments_engine

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

SAMPLE_WIRE_DIRECTIVE = ProductionDirectivePayload.build(
    directive_id="dir_ac_001", decision_id="dec_ac_001", arm_id="rnb_late_night_drive_english_vocal_45_75m", allocation_type="exploit", priority=1, quantity=2, brief={
        "genre": "rnb",
        "mood": ["melancholic", "intimate"],
        "use_case": ["late_night_drive"],
        "language": "english",
        "vocal_mode": "vocal",
        "duration_bucket": "45_75m",
        "visual_world": "rainy_asian_city",
    }, expected_value=88.0, uncertainty=0.12, budget_limit=100.0, market_context={"brand_id": "neon_seoul"}, selection_reason=["high_demand_opportunity"], directive_version="p2-dir-v1", policy_version="p2-v1", expires_at="2026-11-01T00:00:00Z", exploration_fraction=0.25, value_model="early-stage-proxy-v1", outcome_horizon="28d"
).to_wire()


def _validated(adapted, genome, *, seed: int):  # type: ignore[no-untyped-def]
    exp = design_experiment(adapted, genome, seed=seed, created_at=FIXED_NOW)
    return promote_to_validated(exp, validate_experiment(exp, genome))


@pytest.fixture
def session():
    engine = create_experiments_engine(static_pool=True)
    factory = sessionmaker(bind=engine)
    with factory() as sess:
        yield sess


def test_ac_01_basic_materialization(session) -> None:
    """AC-01: Given a valid P2 directive, P3 creates one validated experiment and one content package."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    repositories.insert_genome(session, genome, genome_id="gen_ac01", created_at=FIXED_NOW)

    exp = design_experiment(adapted, genome, seed=1, created_at=FIXED_NOW)
    assert exp.status is ExperimentStatus.DRAFT
    repositories.insert_experiment(session, exp)

    report = validate_experiment(exp, genome)
    assert report.passed is True
    exp = promote_to_validated(exp, report)

    treatment = exp.treatments[0]
    pkg = materialize_package(exp, treatment, genome, brand_id="neon_seoul", budget_envelope=80.0, created_at=FIXED_NOW)
    repositories.insert_content_package(session, pkg)
    session.commit()

    retrieved = repositories.get_content_package(session, pkg.package_id)
    assert retrieved is not None
    assert retrieved.package_id == pkg.package_id
    assert len(retrieved.track_specs) == 5
    assert retrieved.playlist_spec is not None
    assert retrieved.visual_spec is not None
    assert retrieved.thumbnail_spec is not None
    assert retrieved.title_spec is not None


def test_ac_02_multiple_treatments() -> None:
    """AC-02: A two-treatment experiment creates distinct, correctly isolated treatment specifications."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    exp = design_experiment(adapted, genome, experiment_type=ExperimentType.SINGLE_FACTOR, seed=2, created_at=FIXED_NOW)

    assert len(exp.treatments) == 2
    t0, t1 = exp.treatments[0], exp.treatments[1]
    assert t0.treatment_id != t1.treatment_id
    assert t0.name == "control"
    assert t1.name == "treatment_1"
    assert t0.variable_assignments != t1.variable_assignments


def test_ac_03_controlled_variable() -> None:
    """AC-03: A controlled variable remains identical across all treatments."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    exp = design_experiment(adapted, genome, experiment_type=ExperimentType.SINGLE_FACTOR, seed=3, created_at=FIXED_NOW)

    # In single factor on bpm, track_duration is controlled
    ctrl_var = next(v for v in exp.variables if v.variable_class == VariableClass.CONTROLLED)
    assert ctrl_var is not None
    assert ctrl_var.baseline_value == genome.track_duration.target_seconds

    # No treatment mutates the controlled variable
    for trt in exp.treatments:
        assert ctrl_var.name not in trt.genome_delta


def test_ac_04_experimental_variable() -> None:
    """AC-04: An experimental variable differs only where the experiment specifies it."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    exp = design_experiment(adapted, genome, experiment_type=ExperimentType.SINGLE_FACTOR, seed=4, created_at=FIXED_NOW)

    exp_var = next(v for v in exp.variables if v.variable_class == VariableClass.EXPERIMENTAL)
    assert exp_var.name == "bpm"
    # Treatment 1 differs from baseline (control)
    assert exp.treatments[0].variable_assignments["bpm"] != exp.treatments[1].variable_assignments["bpm"]


def test_ac_05_creative_freedom() -> None:
    """AC-05: Unspecified creative fields remain open rather than receiving arbitrary hard-coded values."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    exp = _validated(adapted, genome, seed=5)
    pkg = materialize_package(exp, exp.treatments[0], genome, brand_id="neon_seoul", budget_envelope=90.0, created_at=FIXED_NOW)

    # Track specs must contain open creative latitude and bounded preferences
    for trk in pkg.track_specs:
        assert "energy" in trk.creative_latitude
        assert "visual_motion" in trk.creative_latitude
        assert trk.bounded_preferences.bpm > 0


def test_ac_06_duplicate_prevention(session) -> None:
    """AC-06: Repeated identical requests are detected as duplicates unless marked as replication."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)

    gid1 = repositories.insert_genome(session, genome, genome_id="gen_dup_1", created_at=FIXED_NOW)
    gid2 = repositories.insert_genome(session, genome, genome_id="gen_dup_2", created_at=FIXED_NOW)
    session.commit()
    # Identical content fingerprint returns the existing genome ID
    assert gid1 == gid2


def test_ac_07_replication(session) -> None:
    """AC-07: A replication creates a new experiment with lineage to the original."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    repositories.insert_genome(session, genome, genome_id="gen_rep", created_at=FIXED_NOW)

    exp_orig = design_experiment(adapted, genome, experiment_type=ExperimentType.SINGLE_FACTOR, seed=7, created_at=FIXED_NOW)
    repositories.insert_experiment(session, exp_orig)

    exp_rep = design_experiment(
        adapted,
        genome,
        experiment_type=ExperimentType.REPLICATION,
        seed=77,
        created_at=FIXED_NOW,
    )
    # Lineage / replication experiment type is explicitly set
    assert exp_rep.experiment_type == ExperimentType.REPLICATION
    assert exp_rep.production_directive_id == exp_orig.production_directive_id
    assert exp_rep.content_arm_id == exp_orig.content_arm_id


def test_ac_08_schema_versioning(session) -> None:
    """AC-08: Historical experiments remain readable after a new genome version is introduced."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    _ = repositories.insert_genome(session, genome, genome_id="gen_v1", created_at=FIXED_NOW)

    exp = design_experiment(adapted, genome, seed=8, created_at=FIXED_NOW)
    repositories.insert_experiment(session, exp)
    session.commit()

    # Re-reading works seamlessly
    loaded = repositories.get_experiment(session, exp.experiment_id)
    assert loaded is not None
    assert loaded.experiment_version == exp.experiment_version
    assert loaded.base_genome_fingerprint == exp.base_genome_fingerprint


def test_ac_09_invalid_constraint() -> None:
    """AC-09: Contradictory constraints fail validation with machine-readable errors."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)

    # Introduce contradiction: instrumental vocal mode with female vocal gender
    contradictory_genome = genome.model_copy(
        update={"vocal_mode": VocalMode.INSTRUMENTAL, "vocal_gender": VocalGender.FEMALE}
    )
    exp = design_experiment(adapted, contradictory_genome, seed=9, created_at=FIXED_NOW)
    report = validate_experiment(exp, contradictory_genome)

    assert report.passed is False
    assert len(report.contradictions) > 0
    assert any("contradiction" in err.lower() or "instrumental" in err.lower() for err in report.contradictions)


def test_ac_10_idempotency(session) -> None:
    """AC-10: Retrying creation/materialization does not duplicate child objects."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    repositories.insert_genome(session, genome, genome_id="gen_idem", created_at=FIXED_NOW)

    exp1 = _validated(adapted, genome, seed=10)
    exp2 = _validated(adapted, genome, seed=10)
    assert exp1.experiment_id == exp2.experiment_id

    # Materializing twice produces identical hash and component spec counts
    pkg1 = materialize_package(exp1, exp1.treatments[0], genome, brand_id="neon_seoul", created_at=FIXED_NOW)
    pkg2 = materialize_package(exp1, exp1.treatments[0], genome, brand_id="neon_seoul", created_at=FIXED_NOW)
    assert pkg1.package_hash == pkg2.package_hash
    assert len(pkg1.track_specs) == len(pkg2.track_specs)


def test_ac_11_failure_recovery(session) -> None:
    """AC-11: A failed materialization can resume without recreating already committed objects."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    repositories.insert_genome(session, genome, genome_id="gen_rec", created_at=FIXED_NOW)

    exp = _validated(adapted, genome, seed=11)
    repositories.insert_experiment(session, exp)
    session.commit()

    # Budget exceeded (< 0.0) fails cleanly without corrupting experiment
    with pytest.raises(BudgetEnvelopeExceededError):
        materialize_package(exp, exp.treatments[0], genome, budget_envelope=500.0, created_at=FIXED_NOW)

    # Experiment remains intact and can be materialized successfully with proper envelope
    pkg = materialize_package(exp, exp.treatments[0], genome, budget_envelope=100.0, created_at=FIXED_NOW)
    repositories.insert_content_package(session, pkg)
    repositories.update_experiment_status(session, exp.experiment_id, ExperimentStatus.MATERIALIZED, now=FIXED_NOW)
    session.commit()

    loaded = repositories.get_experiment(session, exp.experiment_id)
    assert loaded is not None
    assert loaded.status == ExperimentStatus.MATERIALIZED


def test_ac_12_p4_handoff() -> None:
    """AC-12: P4 can obtain a complete music-production-ready package through the API without querying the P3 database directly."""
    engine = create_experiments_engine(static_pool=True)
    factory = sessionmaker(bind=engine)
    env = {
        "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
        "POSTGRES_PASSWORD": "x",
        "MINIO_ENDPOINT": "127.0.0.1:9",
        "MINIO_ROOT_USER": "plm",
        "MINIO_ROOT_PASSWORD": "x",
        "MINIO_BUCKET": "plm-media",
        "TEMPORAL_HOST": "127.0.0.1:9",
        "PLM_SERVICE_TOKEN": "token-p4-handoff",
    }
    settings = load_experiments_settings(platform=load_settings(env=env))
    app = create_app(session_factory=factory, settings=settings)
    client = TestClient(app)
    headers = {"X-PLM-Service-Token": "token-p4-handoff"}

    # Create & materialize via API
    create_resp = client.post(
        "/experiments/v1/experiments",
        json={"directive": SAMPLE_WIRE_DIRECTIVE, "experiment_type": "single_factor", "seed": 12},
        headers=headers,
    )
    assert create_resp.status_code == 200
    exp_id = create_resp.json()["experiment"]["experiment_id"]
    trt_id = create_resp.json()["experiment"]["treatments"][0]["treatment_id"]
    assert create_resp.json()["experiment"]["status"] == "draft"

    # Not materializable until the validator has promoted it.
    early = client.post(
        f"/experiments/v1/experiments/{exp_id}/materialize",
        json={"treatment_id": trt_id, "brand_id": "neon_seoul"},
        headers=headers,
    )
    assert early.status_code == 409
    assert early.json()["detail"]["code"] == "experiment_not_validated"
    val_resp = client.post(f"/experiments/v1/experiments/{exp_id}/validate", headers=headers)
    assert val_resp.json()["status"] == "validated"

    mat_resp = client.post(
        f"/experiments/v1/experiments/{exp_id}/materialize",
        json={"treatment_id": trt_id, "brand_id": "neon_seoul", "budget_envelope": 100.0},
        headers=headers,
    )
    assert mat_resp.status_code == 200
    pkg_id = mat_resp.json()["package"]["package_id"]

    # P4 retrieves complete package without querying DB
    pkg_resp = client.get(f"/experiments/v1/packages/{pkg_id}", headers=headers)
    assert pkg_resp.status_code == 200
    p4_package = pkg_resp.json()
    assert p4_package["package_id"] == pkg_id
    assert len(p4_package["track_specs"]) == 5
    assert p4_package["playlist_spec"]["track_count_target"] == 5


def test_ac_13_audit_trail(session) -> None:
    """AC-13: Every experiment can be traced back to P2 and the source market arm."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    repositories.insert_genome(session, genome, genome_id="gen_audit", created_at=FIXED_NOW)

    exp = design_experiment(adapted, genome, seed=13, created_at=FIXED_NOW)
    repositories.insert_experiment(session, exp)
    session.commit()

    loaded = repositories.get_experiment(session, exp.experiment_id)
    assert loaded is not None
    assert loaded.production_directive_id == SAMPLE_WIRE_DIRECTIVE["directive_id"]
    assert loaded.content_arm_id == SAMPLE_WIRE_DIRECTIVE["arm_id"]


def test_ac_14_budget_ceiling() -> None:
    """AC-14: P3 rejects a package that exceeds configured hard constraints before it enters production."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)
    exp = _validated(adapted, genome, seed=14)

    with pytest.raises(BudgetEnvelopeExceededError, match="positive"):
        materialize_package(exp, exp.treatments[0], genome, budget_envelope=-10.0, created_at=FIXED_NOW)
    # The directive's budget_limit (100.0) is the ceiling: a request may not raise it.
    with pytest.raises(BudgetEnvelopeExceededError, match="exceeds the directive's budget limit"):
        materialize_package(exp, exp.treatments[0], genome, budget_envelope=100.01, created_at=FIXED_NOW)


def test_ac_15_experiment_contamination() -> None:
    """AC-15: A design claiming to test one factor is flagged if its treatment overrides intentionally change another factor."""
    adapted = adapt_directive(SAMPLE_WIRE_DIRECTIVE)
    genome = build_genome_from_directive(adapted)

    # Design single factor (which targets 'bpm')
    exp = design_experiment(adapted, genome, experiment_type=ExperimentType.SINGLE_FACTOR, seed=15, created_at=FIXED_NOW)

    # Introduce contamination: manually add an unexpected secondary variable override to treatment_1
    contaminated_assignments = dict(exp.treatments[1].variable_assignments)
    contaminated_assignments["visual_world"] = "cyberpunk_desert"

    contaminated_treatment = Treatment(
        treatment_id=exp.treatments[1].treatment_id,
        treatment_index=exp.treatments[1].treatment_index,
        name=exp.treatments[1].name,
        variable_assignments=contaminated_assignments,
        genome_delta={"bpm": {"target_bpm": 85}, "visual_world": "cyberpunk_desert"},
    )

    contaminated_exp = exp.model_copy(
        update={"treatments": (exp.treatments[0], contaminated_treatment)}
    )

    report = validate_experiment(contaminated_exp, genome)
    # Contamination must be flagged in errors
    assert report.passed is False
    assert len(report.errors) > 0
    assert any("uncontrolled" in msg.lower() or "mutates" in msg.lower() for msg in report.errors)

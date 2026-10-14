"""Controlled production dry-run smoke test for PHASE-P3 (§83, §114).

Runs the full internal pipeline:
synthetic P2 directive → P3 experiment → validate → materialize → retrieve package.
Zero external music/video provider calls; hermetic fail-closed verification.
"""

from __future__ import annotations

from datetime import UTC, datetime

from plm_contracts.seams.c2 import ProductionDirectivePayload
from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..config import load_experiments_settings
from ..domain.enums import ExperimentStatus
from ..experiments.engine import design_experiment
from ..genome.engine import build_genome_from_directive
from ..ingestion.p2_adapter import adapt_directive
from ..materialization.builder import materialize_package
from ..observability.logging import StructuredEvent, emit_event
from ..storage import repositories
from ..storage.ids import deterministic_id
from ..validation.engine import validate_experiment

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending experiments service token (PLM_SERVICE_TOKEN)"


def run_smoke_test(*, brand_id: str | None = None) -> str | None:
    """Run the P3 dry-run smoke test. Returns None on success, or blocked reason string."""
    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON

    _ = load_experiments_settings(platform=platform)
    brand = brand_id or "neon_seoul"

    # Test database connectivity
    engine = build_engine(platform)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any connection failure is the blocked condition
        return f"blocked: database unreachable ({exc})"

    factory = session_factory(engine)
    with factory() as session:
        now = datetime.now(UTC)
        wire_directive = ProductionDirectivePayload.build(directive_id=f"dir_smoke_{int(now.timestamp())}", decision_id=f"dec_smoke_{int(now.timestamp())}", arm_id="rnb_late_night_drive_english_vocal_45_75m", allocation_type="exploit", priority=1, quantity=1, brief={
                "genre": "rnb",
                "mood": ["melancholic", "intimate"],
                "use_case": ["late_night_drive"],
                "language": "english",
                "vocal_mode": "vocal",
                "duration_bucket": "45_75m",
                "visual_world": "rainy_asian_city",
            }, expected_value=85.0, uncertainty=0.15, budget_limit=100.0, market_context={"brand_id": brand}, selection_reason=["smoke_test"], directive_version="p2-dir-v1", policy_version="p2-v1", expires_at="2026-12-31T23:59:59Z", exploration_fraction=0.2, value_model="early-stage-proxy-v1").to_wire()

        # 1. Ingest
        adapted = adapt_directive(wire_directive)

        # 2. Base genome
        base_genome = build_genome_from_directive(adapted)
        gid = deterministic_id("gen", adapted.arm_id, adapted.directive_id)
        repositories.insert_genome(session, base_genome, genome_id=gid)

        # 3. Design experiment
        exp = design_experiment(adapted, base_genome, seed=123)
        repositories.insert_experiment(session, exp)

        # 4. Validate
        report = validate_experiment(exp, base_genome)
        if not report.passed:
            return f"failed: validation errors {report.errors}"

        # 5. Materialize package
        target_treatment = exp.treatments[0]
        pkg = materialize_package(
            exp,
            target_treatment,
            base_genome,
            brand_id=brand,
            budget_envelope=50.0,
            track_count=3,
            total_duration_minutes=45,
        )
        repositories.insert_content_package(session, pkg)
        repositories.update_experiment_status(session, exp.experiment_id, ExperimentStatus.MATERIALIZED)

        # 6. Retrieve package
        stored_pkg = repositories.get_content_package(session, pkg.package_id)
        if stored_pkg is None:
            return f"failed: materialized package {pkg.package_id} not found in database"
        if stored_pkg.package_hash != pkg.package_hash:
            return f"failed: package hash mismatch ({stored_pkg.package_hash} != {pkg.package_hash})"

        # 7. Audit event
        emit_event(
            session,
            StructuredEvent(
                experiment_id=exp.experiment_id,
                event="smoke_test_completed",
                state_before="designed",
                state_after="materialized",
                package_id=pkg.package_id,
            ),
        )
        session.commit()

    return None

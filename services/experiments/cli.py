"""Implementations behind the `plm experiments …` CLI commands (PHASE-P3 §86)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

import uvicorn
from plm_platform.clock import SystemClock
from plm_platform.config import load_settings
from plm_platform.db import session_factory as platform_session_factory
from sqlalchemy.orm import Session

from .api.app import create_app
from .config import load_experiments_settings
from .domain.enums import ExperimentStatus
from .materialization.builder import materialize_package
from .observability.metrics import (
    record_experiment_validated,
    record_package_materialized,
    record_validation_failure,
)
from .storage import repositories
from .validation.engine import promote_to_validated, validate_experiment

__all__ = [
    "DEFAULT_BRAND_ID",
    "list_experiments",
    "materialize_treatment_cli",
    "serve",
    "show_experiment",
    "validate_experiment_cli",
]

DEFAULT_BRAND_ID: Final[str] = "neon_seoul"
SessionFactory = Callable[[], Session]


def _session(session_factory: SessionFactory | None) -> Session:
    if session_factory is not None:
        return session_factory()
    return platform_session_factory()()


def show_experiment(experiment_id: str, *, session_factory: SessionFactory | None = None) -> str:
    """Show details of one experiment definition."""
    with _session(session_factory) as session:
        exp = repositories.get_experiment(session, experiment_id)
        if exp is None:
            raise LookupError(f"experiment {experiment_id!r} does not exist")

    lines = [
        f"experiment {exp.experiment_id} [{exp.status.value}]",
        f"  arm {exp.content_arm_id} · directive {exp.production_directive_id}",
        f"  type {exp.experiment_type.value} · version {exp.experiment_version}",
        f"  hypothesis: {exp.hypothesis.statement}",
        f"  target metric: {exp.hypothesis.target_metric} ({exp.hypothesis.expected_direction})",
        f"  variables ({len(exp.variables)}): " + ", ".join(v.name for v in exp.variables),
        f"  treatments ({len(exp.treatments)}): " + ", ".join(t.name for t in exp.treatments),
        (
            f"  outcome horizon: {exp.measurement_plan.outcome_horizon.value} "
            f"({exp.measurement_plan.maturity_rule.value})"
        ),
        f"  budget limit: {exp.budget_limit}",
        f"  base genome fingerprint: {exp.base_genome_fingerprint[:16]}...",
        f"  created at: {exp.created_at.isoformat()}",
    ]
    return "\n".join(lines)


def list_experiments(limit: int = 20, *, session_factory: SessionFactory | None = None) -> str:
    """List recent experiments."""
    with _session(session_factory) as session:
        exps = repositories.list_experiments(session, limit=limit)

    if not exps:
        return "No experiments found."

    header = f"{'EXPERIMENT ID':<30} {'STATUS':<15} {'TYPE':<16} {'TREATMENTS':<10}"
    rows = [header, "-" * len(header)]
    for e in exps:
        rows.append(
            f"{e.experiment_id:<30} {e.status.value:<15} {e.experiment_type.value:<16} {len(e.treatments):<10}"
        )
    return "\n".join(rows)


def validate_experiment_cli(
    experiment_id: str, *, session_factory: SessionFactory | None = None
) -> str:
    """Validate an experiment definition."""
    with _session(session_factory) as session:
        exp = repositories.get_experiment(session, experiment_id)
        if exp is None:
            raise LookupError(f"experiment {experiment_id!r} does not exist")

        genome = repositories.get_genome_by_fingerprint(session, exp.base_genome_fingerprint)
        if genome is None:
            raise LookupError(f"genome fingerprint {exp.base_genome_fingerprint!r} not found")

        report = validate_experiment(exp, genome)
        record_experiment_validated(passed=report.passed)
        status = exp.status
        if report.passed and exp.status is ExperimentStatus.DRAFT:
            status = promote_to_validated(exp, report).status
            repositories.update_experiment_status(
                session, experiment_id, status, now=SystemClock().now()
            )
            session.commit()
        if not report.passed:
            for err in report.errors:
                record_validation_failure(error_code=err[:32])

    status_str = "PASSED" if report.passed else "FAILED"
    lines = [
        f"Validation {status_str} for experiment {experiment_id}:",
        f"  passed: {report.passed}",
        f"  status: {status.value}",
        f"  errors ({len(report.errors)}): {list(report.errors)}",
        f"  warnings ({len(report.warnings)}): {list(report.warnings)}",
        f"  contradictions ({len(report.contradictions)}): {list(report.contradictions)}",
    ]
    return "\n".join(lines)


def materialize_treatment_cli(
    experiment_id: str,
    treatment_id: str,
    *,
    brand_id: str = DEFAULT_BRAND_ID,
    budget_envelope: float | None = None,
    track_count: int = 5,
    total_duration_minutes: int = 60,
    session_factory: SessionFactory | None = None,
) -> str:
    """Materialize a specific treatment of an experiment into a ContentPackage."""
    with _session(session_factory) as session:
        exp = repositories.get_experiment(session, experiment_id)
        if exp is None:
            raise LookupError(f"experiment {experiment_id!r} does not exist")

        treatment = next((t for t in exp.treatments if t.treatment_id == treatment_id), None)
        if treatment is None:
            raise LookupError(f"treatment {treatment_id!r} not found in experiment {experiment_id}")

        genome = repositories.get_genome_by_fingerprint(session, exp.base_genome_fingerprint)
        if genome is None:
            raise LookupError(f"genome fingerprint {exp.base_genome_fingerprint!r} not found")

        now = SystemClock().now()
        package = materialize_package(
            exp,
            treatment,
            genome,
            created_at=now,
            brand_id=brand_id,
            budget_envelope=budget_envelope,
            track_count=track_count,
            total_duration_minutes=total_duration_minutes,
        )

        repositories.insert_content_package(session, package)
        repositories.update_experiment_status(
            session, experiment_id, ExperimentStatus.MATERIALIZED, now=now
        )
        session.commit()
        record_package_materialized(brand_id=brand_id)

    lines = [
        f"Materialized package {package.package_id}:",
        f"  experiment: {package.experiment_id}",
        f"  treatment: {package.treatment_id}",
        f"  brand: {package.brand_id}",
        f"  status: {package.status.value}",
        f"  tracks: {len(package.track_specs)}",
        f"  playlist tracks: {package.playlist_spec.track_count_target}",
        f"  visual world: {package.visual_spec.visual_world}",
        f"  thumbnail: {package.thumbnail_spec.composition_archetype}",
        f"  title archetype: {package.title_spec.title_archetype}",
        f"  package hash: {package.package_hash}",
    ]
    return "\n".join(lines)


def serve(host: str = "127.0.0.1", port: int = 8083) -> None:
    """Run uvicorn dev server for the /experiments API."""
    settings = load_experiments_settings(platform=load_settings())
    factory = platform_session_factory()
    app = create_app(session_factory=factory, settings=settings)
    uvicorn.run(app, host=host, port=port)

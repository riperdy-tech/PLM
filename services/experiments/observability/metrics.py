"""Observability metrics for PHASE-P3 (§79).

Uses `get_or_create_counter` to avoid `DuplicateTimeseries` errors on repeated imports.
"""

from __future__ import annotations

from plm_platform.observability import get_or_create_counter

__all__ = [
    "EXPERIMENTS_CANCELLED",
    "EXPERIMENTS_CREATED",
    "EXPERIMENTS_DUPLICATE_ATTEMPTS",
    "EXPERIMENTS_MATERIALIZATION_FAILURES",
    "EXPERIMENTS_MATERIALIZED",
    "EXPERIMENTS_REJECTED",
    "EXPERIMENTS_VALIDATED",
    "EXPERIMENTS_VALIDATION_FAILURES",
    "record_duplicate_attempt",
    "record_experiment_created",
    "record_experiment_validated",
    "record_materialization_failure",
    "record_package_materialized",
    "record_validation_failure",
]

EXPERIMENTS_CREATED = get_or_create_counter(
    "plm_experiments_created_total",
    "Total experiments created.",
    ["experiment_type", "brand_id"],
)

EXPERIMENTS_VALIDATED = get_or_create_counter(
    "plm_experiments_validated_total",
    "Total experiment validation checks executed.",
    ["passed"],
)

EXPERIMENTS_REJECTED = get_or_create_counter(
    "plm_experiments_rejected_total",
    "Total experiments rejected during validation.",
    ["reason"],
)

EXPERIMENTS_MATERIALIZED = get_or_create_counter(
    "plm_experiments_materialized_total",
    "Total content packages materialized.",
    ["brand_id"],
)

EXPERIMENTS_CANCELLED = get_or_create_counter(
    "plm_experiments_cancelled_total",
    "Total experiments cancelled.",
    ["reason"],
)

EXPERIMENTS_MATERIALIZATION_FAILURES = get_or_create_counter(
    "plm_experiments_materialization_failures_total",
    "Total materialization failures.",
    ["error_code"],
)

EXPERIMENTS_VALIDATION_FAILURES = get_or_create_counter(
    "plm_experiments_validation_failures_total",
    "Total validation failures.",
    ["error_code"],
)

EXPERIMENTS_DUPLICATE_ATTEMPTS = get_or_create_counter(
    "plm_experiments_duplicate_attempts_total",
    "Total duplicate experiment creation or materialization attempts.",
    ["entity"],
)


def record_experiment_created(*, experiment_type: str, brand_id: str) -> None:
    """Record an experiment creation event."""
    EXPERIMENTS_CREATED.labels(experiment_type=experiment_type, brand_id=brand_id).inc()


def record_experiment_validated(*, passed: bool) -> None:
    """Record an experiment validation attempt."""
    EXPERIMENTS_VALIDATED.labels(passed=str(passed).lower()).inc()


def record_package_materialized(*, brand_id: str) -> None:
    """Record a package materialization event."""
    EXPERIMENTS_MATERIALIZED.labels(brand_id=brand_id).inc()


def record_materialization_failure(*, error_code: str) -> None:
    """Record a materialization failure."""
    EXPERIMENTS_MATERIALIZATION_FAILURES.labels(error_code=error_code).inc()


def record_validation_failure(*, error_code: str) -> None:
    """Record a validation failure."""
    EXPERIMENTS_VALIDATION_FAILURES.labels(error_code=error_code).inc()


def record_duplicate_attempt(*, entity: str) -> None:
    """Record a duplicate attempt."""
    EXPERIMENTS_DUPLICATE_ATTEMPTS.labels(entity=entity).inc()

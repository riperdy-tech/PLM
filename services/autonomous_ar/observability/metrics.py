# services/autonomous_ar/observability/metrics.py
"""§87's counters on the platform registry. `get_or_create_counter` only — P1's fix-round
rule: a bare `Counter` runs twice against the shared registry under two module identities
(`services.autonomous_ar…` and the editable top-level `autonomous_ar…`), and the second
import raises `DuplicateTimeseries` in-process.

Every figure here is a count (whole events), never a rate and never money: the money-shaped
fields P2 handles (`budget_limit`, `assumed_cost_per_directive`) stay on the persisted rows,
where their unit is documented once (cycle budget units, ADR-0023 #1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plm_platform.observability import get_or_create_counter

if TYPE_CHECKING:
    from ..modeling.update import UpdateSummary

__all__ = [
    "ARM_REACTIVATIONS", "ARM_RETIREMENTS", "CONSTRAINT_REJECTIONS", "CYCLES_TOTAL",
    "CYCLE_FAILURES", "POSTERIOR_UPDATES_TOTAL", "POSTERIOR_UPDATE_FAILURES", "record_cycle",
    "record_posterior_updates",
]

CYCLES_TOTAL = get_or_create_counter("plm_ar_allocation_cycles_total",
                                     "Allocation cycles finished, by status.", ["status"])
CYCLE_FAILURES = get_or_create_counter("plm_ar_allocation_cycle_failures",
                                       "Cycles that failed to finish.", [])
POSTERIOR_UPDATES_TOTAL = get_or_create_counter("plm_ar_posterior_updates_total",
                                                "Posterior updates applied.", [])
POSTERIOR_UPDATE_FAILURES = get_or_create_counter("plm_ar_posterior_update_failures",
                                                  "Posterior updates failed or quarantined.", [])
CONSTRAINT_REJECTIONS = get_or_create_counter("plm_ar_constraint_rejections_total",
                                              "Constraint rejections, by reason.", ["reason"])
ARM_RETIREMENTS = get_or_create_counter("plm_ar_arm_retirements_total", "Arms retired.", [])
ARM_REACTIVATIONS = get_or_create_counter("plm_ar_arm_reactivations_total", "Arms reopened.", [])


def record_cycle(*, status: str, eligible: int, selected: int, rejected: int,
                 retirements: int = 0, reactivations: int = 0) -> None:
    """One call per finished cycle; `eligible`/`selected`/`rejected` are counts, not rates.

    `eligible` and `rejected` are accepted for §87's shape and future gauges; the counters
    this module freezes emit `status`, the §38/§39 events and (from the cycle tail)
    per-reason rejections through :data:`CONSTRAINT_REJECTIONS`.
    """
    CYCLES_TOTAL.labels(status=status).inc()
    if retirements:
        ARM_RETIREMENTS.inc(retirements)
    if reactivations:
        ARM_REACTIVATIONS.inc(reactivations)


def record_posterior_updates(summary: UpdateSummary) -> None:
    """One call per update pass; applied, failed and quarantined are disjoint counts."""
    POSTERIOR_UPDATES_TOTAL.inc(summary.applied)
    POSTERIOR_UPDATE_FAILURES.inc(summary.failed + summary.quarantined)

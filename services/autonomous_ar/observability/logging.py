# services/autonomous_ar/observability/logging.py
"""§94's one structured line per cycle and §88's alert codes.

The line carries `brand_id` where §94's draft says `channel_id` — ADR-0019 made brand the
cycle's coordinate and `channel_id` is not a wire name. `runtime_ms` is a monotonic
duration (seconds converted, measured by the caller), never a wall-clock read. Alerts and
the cycle log both go through `logging`, so the platform's JSON handler stamps the
correlation id automatically (`plm_platform.observability`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from ..domain.policy import PolicyConfig

if TYPE_CHECKING:
    from ..decisioning.cycle import CycleResult

__all__ = [
    "ALL_SELECTED_ONE_CLUSTER", "EXPLORATION_BELOW_FLOOR", "STALE_P1_DATA",
    "ZERO_ELIGIBLE_ARMS", "emit_alert", "evaluate_cycle_alerts", "log_cycle",
]

_cycle_logger = logging.getLogger("autonomous_ar.cycle")
_alert_logger = logging.getLogger("autonomous_ar.alerts")

ZERO_ELIGIBLE_ARMS = "ZERO_ELIGIBLE_ARMS"
STALE_P1_DATA = "STALE_P1_DATA"
ALL_SELECTED_ONE_CLUSTER = "ALL_SELECTED_ONE_CLUSTER"
EXPLORATION_BELOW_FLOOR = "EXPLORATION_BELOW_FLOOR"
"""§88's codes this service emits: "zero eligible arms", "stale P1 data", "all selected
arms from one cluster", "exploration fraction below configured minimum"."""


def log_cycle(result: CycleResult, *, brand_id: str, policy_version: str,
              p1_snapshot_time: datetime | None, runtime_ms: float) -> None:
    """One line per finished (non-replay) cycle; the extras are §94's field list.

    `arm_count` counts the arms this cycle's record touched (selections + refusals) —
    the candidate set itself is the engine's, not the line's.
    """
    selected_ids = [directive.arm_id for directive in result.directives]
    allocation_by_arm = {directive.arm_id: directive.allocation_type.value
                         for directive in result.directives}
    constraint_codes = [rejection.reason.value for rejection in result.rejected]
    _cycle_logger.info(
        "allocation cycle %s finished as %s", result.cycle_id, result.status.value,
        extra={
            "cycle_id": result.cycle_id, "brand_id": brand_id,
            "status": result.status.value, "policy_version": policy_version,
            "p1_snapshot_time": (None if p1_snapshot_time is None
                                 else p1_snapshot_time.isoformat()),
            "arm_count": len(result.directives) + len(result.rejected),
            "selected_ids": selected_ids, "allocation_type_by_arm": allocation_by_arm,
            "constraints_triggered": constraint_codes, "warnings": list(result.warnings),
            "runtime_ms": runtime_ms, "replay": result.replay,
        },
    )


def evaluate_cycle_alerts(*, eligible: int, stale: int,
                          selections: Sequence[tuple[str, str]],
                          exploration_fraction: float,
                          policy: PolicyConfig) -> tuple[str, ...]:
    """§88's four codes as a pure function — no logging, so it is testable in isolation.

    `selections` carries `(arm_id, cluster_id)` pairs: "all selected arms from one
    cluster" is decidable only with cluster membership at alert time, so the caller hands
    it in (the live cycle supplies the planner's cluster map). The exploration floor is
    the policy's configured exploration slices (`adjacent + radical`).

    Note on coverage, said plainly: the cycle tail wires this function into the live path,
    but the end-to-end case cannot be staged through a stored fixture alone (it needs a
    multi-cluster portfolio of the right shape); the function's contract is what the tests
    exercise — the wiring call itself is the same one production makes.
    """
    codes: list[str] = []
    if eligible <= 0:
        codes.append(ZERO_ELIGIBLE_ARMS)
    if stale > 0:
        codes.append(STALE_P1_DATA)
    if selections and len({cluster for _, cluster in selections}) == 1:
        codes.append(ALL_SELECTED_ONE_CLUSTER)
    floor = policy.adjacent_explore_fraction + policy.radical_explore_fraction
    if exploration_fraction < floor - 1e-9:
        codes.append(EXPLORATION_BELOW_FLOOR)
    return tuple(codes)


def emit_alert(code: str, *, brand_id: str, detail: str) -> None:
    """WARNING with the code as prose and as `extra["alert_code"]` (machine-readable)."""
    _alert_logger.warning("alert %s for %s: %s", code, brand_id, detail,
                          extra={"alert_code": code, "brand_id": brand_id})

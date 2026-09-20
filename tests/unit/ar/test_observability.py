# tests/unit/ar/test_observability.py
"""P2.14: §87's counters, §94's structured cycle log and §88's alert codes."""

import logging

from services.autonomous_ar.decisioning.cycle import CycleResult
from services.autonomous_ar.domain.enums import AllocationCycleStatus
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.observability.logging import (
    emit_alert,
    evaluate_cycle_alerts,
    log_cycle,
)
from services.autonomous_ar.observability.metrics import (
    ARM_RETIREMENTS,
    CONSTRAINT_REJECTIONS,
    CYCLES_TOTAL,
    record_cycle,
)


def test_counters_exist_and_increment() -> None:
    # prometheus labeled counter: the sample lives on `.labels(...)`, not the parent
    before = CYCLES_TOTAL.labels(status="completed")._value.get()
    record_cycle(status="completed", eligible=3, selected=2, rejected=1)
    assert CYCLES_TOTAL.labels(status="completed")._value.get() == before + 1
    assert CONSTRAINT_REJECTIONS is not None and ARM_RETIREMENTS is not None


def test_log_cycle_emits_one_structured_line_with_section_94_fields(caplog) -> None:
    result = CycleResult(cycle_id="c-1", status=AllocationCycleStatus.COMPLETED,
                         directives=(), rejected=(), warnings=(), replay=False)
    with caplog.at_level(logging.INFO):
        log_cycle(result, brand_id="neon_seoul", policy_version="p2-v1",
                  p1_snapshot_time=None, runtime_ms=12)
    record = next(record for record in caplog.records
                  if "allocation cycle" in record.getMessage())
    assert record.cycle_id == "c-1"
    assert record.brand_id == "neon_seoul"
    assert record.policy_version == "p2-v1"
    assert record.runtime_ms == 12
    assert record.selected_ids == []
    assert record.allocation_type_by_arm == {}


def test_alert_codes_fire_on_the_documented_conditions() -> None:
    assert "ZERO_ELIGIBLE_ARMS" in evaluate_cycle_alerts(
        eligible=0, stale=0, selections=(), exploration_fraction=0.0, policy=DEFAULT_POLICY)
    assert "STALE_P1_DATA" in evaluate_cycle_alerts(
        eligible=3, stale=1, selections=(("a", "b"),), exploration_fraction=0.3,
        policy=DEFAULT_POLICY)
    assert "ALL_SELECTED_ONE_CLUSTER" in evaluate_cycle_alerts(
        eligible=3, stale=0, selections=(("a", "b"),), exploration_fraction=0.3,
        policy=DEFAULT_POLICY)
    assert "EXPLORATION_BELOW_FLOOR" in evaluate_cycle_alerts(
        eligible=3, stale=0, selections=(("c", "d"),), exploration_fraction=0.0,
        policy=DEFAULT_POLICY)


def test_all_selected_one_cluster_needs_the_cluster_coordinate() -> None:
    """The alert is decidable only with cluster membership at alert time: two arms in two
    clusters raise nothing, and nothing else is wrong with this data."""
    codes = evaluate_cycle_alerts(eligible=3, stale=0,
                                  selections=(("a", "c1"), ("b", "c2")),
                                  exploration_fraction=0.3, policy=DEFAULT_POLICY)
    assert codes == ()


def test_emit_alert_logs_at_warning_and_carries_the_code(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        emit_alert("STALE_P1_DATA", brand_id="neon_seoul", detail="cycle c-1")
    record = next(record for record in caplog.records if record.levelno == logging.WARNING)
    assert record.alert_code == "STALE_P1_DATA"
    assert "STALE_P1_DATA" in record.getMessage()

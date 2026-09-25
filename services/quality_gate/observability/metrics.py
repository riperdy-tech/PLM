"""Observability metrics for Quality Gate (P5 §4.5, §94)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..domain.enums import GateDisposition, ResolutionCode

__all__ = ["QualityGateMetricsCollector", "metrics"]


@dataclass
class QualityGateMetricsCollector:
    """In-memory metric counters for quality gating operations."""

    evaluations_total: int = 0
    evaluations_passed: int = 0
    evaluations_warnings: int = 0
    evaluations_quarantined: int = 0
    evaluations_rejected: int = 0
    quarantine_resolved_override: int = 0
    quarantine_resolved_rejected: int = 0
    dispositions_count: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record_evaluation(self, disposition: GateDisposition) -> None:
        self.evaluations_total += 1
        self.dispositions_count[disposition.value] += 1
        if disposition == GateDisposition.PASSED:
            self.evaluations_passed += 1
        elif disposition == GateDisposition.PASSED_WITH_WARNINGS:
            self.evaluations_warnings += 1
        elif disposition == GateDisposition.QUARANTINED:
            self.evaluations_quarantined += 1
        elif disposition == GateDisposition.REJECTED:
            self.evaluations_rejected += 1

    def record_quarantine_resolution(self, resolution: ResolutionCode) -> None:
        if resolution == ResolutionCode.CLEARED_BY_OVERRIDE:
            self.quarantine_resolved_override += 1
        elif resolution == ResolutionCode.CONFIRMED_REJECTED:
            self.quarantine_resolved_rejected += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "evaluations_total": self.evaluations_total,
            "evaluations_passed": self.evaluations_passed,
            "evaluations_warnings": self.evaluations_warnings,
            "evaluations_quarantined": self.evaluations_quarantined,
            "evaluations_rejected": self.evaluations_rejected,
            "quarantine_resolved_override": self.quarantine_resolved_override,
            "quarantine_resolved_rejected": self.quarantine_resolved_rejected,
            "dispositions": dict(self.dispositions_count),
        }


metrics = QualityGateMetricsCollector()

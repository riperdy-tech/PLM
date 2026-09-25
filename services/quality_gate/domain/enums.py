"""Domain enumerations for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine)."""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CheckSeverity",
    "CheckStatus",
    "EvaluationStatus",
    "GateCategory",
    "GateDisposition",
    "QuarantineStatus",
    "ResolutionCode",
]


class GateDisposition(str, Enum):
    """Final release-gate disposition for a candidate (P5 §51, R8)."""

    PASSED = "PASSED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    CLEARED_BY_OVERRIDE = "CLEARED_BY_OVERRIDE"


class EvaluationStatus(str, Enum):
    """Lifecycle status of a candidate evaluation run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CheckSeverity(str, Enum):
    """Severity of a check finding (P5 §45)."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CheckStatus(str, Enum):
    """Outcome of an individual check."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class GateCategory(str, Enum):
    """Category of gate check (P5 §46)."""

    TECHNICAL_GATE = "TECHNICAL_GATE"
    QUALITY_GATE = "QUALITY_GATE"
    PROVENANCE_GATE = "PROVENANCE_GATE"
    COMMERCIAL_USE_GATE = "COMMERCIAL_USE_GATE"
    RIGHTS_RISK_GATE = "RIGHTS_RISK_GATE"
    POLICY_GATE = "POLICY_GATE"
    EXPERIMENT_INTEGRITY_GATE = "EXPERIMENT_INTEGRITY_GATE"


class QuarantineStatus(str, Enum):
    """Status of an opened quarantine case (P5 §91)."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class ResolutionCode(str, Enum):
    """Resolution disposition for a quarantine review case (P5 §91)."""

    CLEARED_BY_OVERRIDE = "CLEARED_BY_OVERRIDE"
    CONFIRMED_REJECTED = "CONFIRMED_REJECTED"
    RE_EVALUATE = "RE_EVALUATE"

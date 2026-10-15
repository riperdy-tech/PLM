"""Recording a detector result as a rights check plus an evidence item (R4).

Every detector result, whatever its outcome, is persisted twice: as a ``RightsCheck``
(``similarity_score``, ``matched_reference_id``, the full result in ``evidence_data``)
and as an ``EvidenceItem`` whose ``content_hash`` is the SHA-256 of the canonical result
JSON and whose ``summary`` is that JSON, so the score and the reference snapshot it was
computed against are auditable after the fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ..domain.enums import CheckStatus
from ..domain.models import EvidenceItem, RightsCheck, RiskFinding
from .outcome import DetectorOutcome, DetectorResult

__all__ = ["DetectorEvaluation", "detector_records"]


@dataclass(frozen=True, slots=True)
class DetectorEvaluation:
    """What one detector contributes to an evaluation."""

    results: tuple[DetectorResult, ...]
    checks: tuple[RightsCheck, ...]
    findings: tuple[RiskFinding, ...]
    evidence: tuple[EvidenceItem, ...]

    @property
    def hold_codes(self) -> tuple[str, ...]:
        return tuple(sorted({f.finding_code for f in self.findings if not f.blocking}))

_STATUS = {
    DetectorOutcome.MATCH: (CheckStatus.FAIL, "HIGH", 0.9),
    DetectorOutcome.NO_MATCH: (CheckStatus.PASS, "LOW", 0.8),
    DetectorOutcome.UNKNOWN: (CheckStatus.WARN, "UNKNOWN", 0.0),
    DetectorOutcome.NOT_APPLICABLE: (CheckStatus.SKIPPED, "NONE", 1.0),
}


def detector_records(
    evaluation_id: str, result: DetectorResult, *, check_type: str, now: datetime
) -> tuple[RightsCheck, EvidenceItem]:
    status, risk_level, confidence = _STATUS[result.outcome]
    payload = result.as_dict()
    check = RightsCheck.create(
        evaluation_id_str=evaluation_id,
        check_type=check_type,
        status=status,
        risk_level=risk_level,
        confidence=confidence,
        source_type=f"detector:{result.detector}",
        source_ref=(result.source_snapshot_ref or f"{result.source}:unread")[:128],
        matched_reference_id=result.matched_ref,
        similarity_score=result.score if result.score is not None else 0.0,
        evidence_data=payload,
        now=now,
    )
    evidence = EvidenceItem.create(
        evaluation_id_str=evaluation_id,
        evidence_type=check_type,
        source=result.version[:64],
        source_reference=(result.matched_ref or result.source_snapshot_ref or result.source)[:128],
        content_hash=result.content_hash(),
        summary=json.dumps(payload, sort_keys=True, default=str),
        now=now,
    )
    return check, evidence

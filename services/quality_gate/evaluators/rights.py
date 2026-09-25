"""Provenance and rights validator for Phase P5 (P5 §26-§35, §86, Ruling R6).

Enforces mandatory Ruling R6 provenance records:
- plan_or_tier
- account_identity_ref
- terms_version
- downloaded_at

Distinguishes commercial-use eligibility from copyright protection status.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import EvidenceItem, ProvenanceCheck, RightsCheck, RiskFinding

__all__ = ["RightsValidationResult", "validate_provenance_and_rights"]


class RightsValidationResult:
    """Aggregated output of rights and provenance validation."""

    def __init__(
        self,
        rights_checks: list[RightsCheck],
        provenance_checks: list[ProvenanceCheck],
        findings: list[RiskFinding],
        evidence: list[EvidenceItem],
        commercial_use_status: str,
        provenance_status: str,
        rights_risk: str,
    ) -> None:
        self.rights_checks = rights_checks
        self.provenance_checks = provenance_checks
        self.findings = findings
        self.evidence = evidence
        self.commercial_use_status = commercial_use_status
        self.provenance_status = provenance_status
        self.rights_risk = rights_risk

    @property
    def is_cleared(self) -> bool:
        return (
            self.commercial_use_status == "ELIGIBLE"
            and self.provenance_status == "COMPLETE"
            and self.rights_risk in ("LOW", "NONE")
            and not any(f.blocking for f in self.findings)
        )

    @property
    def requires_quarantine(self) -> bool:
        return any(
            f.severity in (CheckSeverity.HIGH, CheckSeverity.MEDIUM) and not f.blocking
            for f in self.findings
        ) or self.commercial_use_status == "UNKNOWN"


def validate_provenance_and_rights(
    evaluation_id: str,
    rights_snapshot: dict[str, Any] | None,
    provenance_metadata: dict[str, Any] | None = None,
) -> RightsValidationResult:
    """Validate candidate licensing and provenance according to Ruling R6."""
    rights_checks: list[RightsCheck] = []
    provenance_checks: list[ProvenanceCheck] = []
    findings: list[RiskFinding] = []
    evidence: list[EvidenceItem] = []

    # 1. Ruling R6 Provenance Verification
    r6_fields = ["plan_or_tier", "account_identity_ref", "terms_version", "downloaded_at"]
    missing_r6 = []

    snapshot = rights_snapshot or {}
    for f in r6_fields:
        val = snapshot.get(f)
        if not val or not str(val).strip():
            missing_r6.append(f)

    if missing_r6:
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="INCOMPLETE_R6_PROVENANCE",
                category=GateCategory.PROVENANCE_GATE,
                severity=CheckSeverity.HIGH,
                status="FAIL",
                confidence=1.0,
                message=f"Missing mandatory Ruling R6 provenance fields: {', '.join(missing_r6)}",
                blocking=False,  # Quarantines for review rather than immediate destruction
            )
        )
        provenance_status = "INCOMPLETE"
        provenance_checks.append(
            ProvenanceCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="r6_provenance_completeness",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.HIGH,
                details={"missing_fields": missing_r6},
            )
        )
    else:
        provenance_status = "COMPLETE"
        provenance_checks.append(
            ProvenanceCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="r6_provenance_completeness",
                status=CheckStatus.PASS,
                severity=CheckSeverity.INFO,
                details={"verified_fields": r6_fields},
            )
        )

    # 2. Commercial use eligibility
    commercial_status_raw = str(snapshot.get("commercial_use_status", "UNKNOWN")).upper()
    plan_tier = str(snapshot.get("plan_or_tier", "")).lower()

    # Rule: Free/unpaid tier cannot be commercially eligible
    if plan_tier in ("free", "unpaid", "trial_free"):
        commercial_use_status = "INELIGIBLE"
        rights_risk = "CRITICAL"
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="COMMERCIAL_USE_INELIGIBLE_TIER",
                category=GateCategory.COMMERCIAL_USE_GATE,
                severity=CheckSeverity.CRITICAL,
                status="FAIL",
                confidence=1.0,
                message=f"Generation was produced on non-commercial plan tier '{plan_tier}'.",
                blocking=True,
            )
        )
        rights_checks.append(
            RightsCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="commercial_use_eligibility",
                status=CheckStatus.FAIL,
                risk_level="CRITICAL",
                confidence=1.0,
                source_type="provider_license_snapshot",
                source_ref=snapshot.get("provider_id", "unknown"),
                evidence_data={"plan_or_tier": plan_tier, "status": "INELIGIBLE"},
            )
        )
    elif commercial_status_raw == "ELIGIBLE" or plan_tier in ("pro", "premier", "standard", "commercial"):
        commercial_use_status = "ELIGIBLE"
        rights_risk = "LOW"
        rights_checks.append(
            RightsCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="commercial_use_eligibility",
                status=CheckStatus.PASS,
                risk_level="LOW",
                confidence=0.95,
                source_type="provider_license_snapshot",
                source_ref=snapshot.get("provider_id", "unknown"),
                evidence_data={"plan_or_tier": plan_tier, "status": "ELIGIBLE"},
            )
        )
    else:
        # Unknown license status -> Quarantine (P5 §5.4: "Unknown is not pass")
        commercial_use_status = "UNKNOWN"
        rights_risk = "MEDIUM"
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="UNKNOWN_COMMERCIAL_LICENSE",
                category=GateCategory.COMMERCIAL_USE_GATE,
                severity=CheckSeverity.MEDIUM,
                status="WARN",
                confidence=0.7,
                message="Commercial license eligibility cannot be verified from provider records.",
                blocking=False,
            )
        )
        rights_checks.append(
            RightsCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="commercial_use_eligibility",
                status=CheckStatus.WARN,
                risk_level="MEDIUM",
                confidence=0.5,
                source_type="provider_license_snapshot",
                source_ref=snapshot.get("provider_id", "unknown"),
                evidence_data={"plan_or_tier": plan_tier, "status": "UNKNOWN"},
            )
        )

    # 3. Create evidence item for rights audit
    evidence.append(
        EvidenceItem.create(
            evaluation_id_str=evaluation_id,
            evidence_type="rights_license_snapshot",
            source="music_factory_r6",
            source_reference=str(snapshot.get("account_identity_ref", "unknown")),
            content_hash=snapshot.get("terms_version", "unknown"),
            summary=f"Commercial status: {commercial_use_status}, Provenance: {provenance_status}",
        )
    )

    return RightsValidationResult(
        rights_checks=rights_checks,
        provenance_checks=provenance_checks,
        findings=findings,
        evidence=evidence,
        commercial_use_status=commercial_use_status,
        provenance_status=provenance_status,
        rights_risk=rights_risk,
    )

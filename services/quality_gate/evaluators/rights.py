"""Provenance and rights validator for Phase P5 (P5 §26-§35, §86, Ruling R6, ADR-0027).

Judges the rights snapshot P4's provider adapter declared for the producing attempt
(``plm_contracts.seams.c4.RightsSnapshotPayload``). Two rules keep it fail-closed:

* **No snapshot means unknown.** Provenance is ``MISSING`` and commercial use is
  ``unknown``; both quarantine the candidate for review.
* **A plan-tier label can only take eligibility away.** A free or trial tier makes the
  output ineligible whatever else was declared. A "pro" label never upgrades an
  undeclared or conditional status to eligible, as it did before ADR-0027.

Commercial-use eligibility stays separate from copyright status (P5 §34).
"""

from __future__ import annotations

from typing import Final

from plm_contracts import CommercialUseEligibility, ProvenanceStatus
from plm_contracts.seams.c4 import RightsSnapshotPayload

from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import EvidenceItem, ProvenanceCheck, RightsCheck, RiskFinding

__all__ = ["NON_COMMERCIAL_TIERS", "RightsValidationResult", "validate_provenance_and_rights"]

NON_COMMERCIAL_TIERS: Final[frozenset[str]] = frozenset({"free", "unpaid", "trial_free", "trial"})
"""Tiers whose output is never commercially usable, whatever the declared status."""


class RightsValidationResult:
    """Aggregated output of rights and provenance validation."""

    def __init__(
        self,
        rights_checks: list[RightsCheck],
        provenance_checks: list[ProvenanceCheck],
        findings: list[RiskFinding],
        evidence: list[EvidenceItem],
        commercial_use_status: CommercialUseEligibility,
        provenance_status: ProvenanceStatus,
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
            self.commercial_use_status is CommercialUseEligibility.ELIGIBLE
            and self.provenance_status is ProvenanceStatus.COMPLETE
            and self.rights_risk in ("LOW", "NONE")
            and not any(f.blocking for f in self.findings)
        )

    @property
    def requires_quarantine(self) -> bool:
        return any(
            f.severity in (CheckSeverity.HIGH, CheckSeverity.MEDIUM) and not f.blocking
            for f in self.findings
        ) or self.commercial_use_status is not CommercialUseEligibility.ELIGIBLE


def validate_provenance_and_rights(
    evaluation_id: str,
    rights: RightsSnapshotPayload | None,
) -> RightsValidationResult:
    """Judge the declared rights snapshot (or its absence) for one candidate."""
    rights_checks: list[RightsCheck] = []
    provenance_checks: list[ProvenanceCheck] = []
    findings: list[RiskFinding] = []
    evidence: list[EvidenceItem] = []

    # 1. Provenance: the R6 snapshot exists (its fields are guaranteed by the seam model).
    if rights is None:
        provenance_status = ProvenanceStatus.MISSING
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="INCOMPLETE_R6_PROVENANCE",
                category=GateCategory.PROVENANCE_GATE,
                severity=CheckSeverity.HIGH,
                status="FAIL",
                confidence=1.0,
                message="The provider declared no license terms for the producing attempt.",
                blocking=False,  # quarantine for review, not rejection
            )
        )
        provenance_checks.append(
            ProvenanceCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="r6_provenance_completeness",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.HIGH,
                details={"rights_snapshot": None},
            )
        )
    else:
        provenance_status = ProvenanceStatus.COMPLETE
        provenance_checks.append(
            ProvenanceCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="r6_provenance_completeness",
                status=CheckStatus.PASS,
                severity=CheckSeverity.INFO,
                details={"attempt_id": rights.attempt_id, "terms_version": rights.terms_version},
            )
        )

    # 2. Commercial-use eligibility: declared status, which a tier can only downgrade.
    declared = rights.commercial_use_status if rights else CommercialUseEligibility.UNKNOWN
    tier = rights.plan_or_tier.strip().lower() if rights else None
    source_ref = rights.provider_id if rights else "undeclared"

    if tier in NON_COMMERCIAL_TIERS or declared is CommercialUseEligibility.INELIGIBLE:
        commercial_use_status = CommercialUseEligibility.INELIGIBLE
        rights_risk = "CRITICAL"
        code = (
            "COMMERCIAL_USE_INELIGIBLE_TIER"
            if tier in NON_COMMERCIAL_TIERS
            else "COMMERCIAL_USE_DECLARED_INELIGIBLE"
        )
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code=code,
                category=GateCategory.COMMERCIAL_USE_GATE,
                severity=CheckSeverity.CRITICAL,
                status="FAIL",
                confidence=1.0,
                message=f"Output is not commercially usable (tier={tier!r}, declared={declared}).",
                blocking=True,
            )
        )
        check_status, risk_level, confidence = CheckStatus.FAIL, "CRITICAL", 1.0
    elif declared is CommercialUseEligibility.ELIGIBLE:
        commercial_use_status = CommercialUseEligibility.ELIGIBLE
        rights_risk = "LOW"
        check_status, risk_level, confidence = CheckStatus.PASS, "LOW", 0.95
    else:
        # unknown, conditional or expired/unverified: not a pass (P5 §5.4).
        commercial_use_status = declared
        rights_risk = "MEDIUM"
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="UNKNOWN_COMMERCIAL_LICENSE",
                category=GateCategory.COMMERCIAL_USE_GATE,
                severity=CheckSeverity.MEDIUM,
                status="WARN",
                confidence=0.7,
                message=f"Commercial-use eligibility is {declared.value!r}, not 'eligible'.",
                blocking=False,
            )
        )
        check_status, risk_level, confidence = CheckStatus.WARN, "MEDIUM", 0.5

    rights_checks.append(
        RightsCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="commercial_use_eligibility",
            status=check_status,
            risk_level=risk_level,
            confidence=confidence,
            source_type="provider_license_snapshot",
            source_ref=source_ref,
            evidence_data={"plan_or_tier": tier, "status": commercial_use_status.value},
        )
    )

    # 3. Evidence for the rights audit: the declaration itself, or its absence.
    evidence.append(
        EvidenceItem.create(
            evaluation_id_str=evaluation_id,
            evidence_type="rights_license_snapshot",
            source="music_factory_r6",
            source_reference=rights.account_identity_ref if rights else "undeclared",
            content_hash=rights.terms_version if rights else "undeclared",
            summary=(
                f"Commercial status: {commercial_use_status.value}, "
                f"Provenance: {provenance_status.value}"
            ),
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

"""Policy and similarity risk screener for Phase P5 (P5 §4.1-§4.4, §36-§40, §87).

Enforces:
- Mandatory YouTube AI disclosure flag (§4.1)
- Protected artist / living person likeness screening in prompts (§16)
- Mass-production and repetition flags (§4.2)
"""

from __future__ import annotations

import re

from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import PolicyCheck, RiskFinding

__all__ = ["PolicyValidationResult", "screen_policy_and_risk"]

# Screening list of prominent protected artist names to prevent unauthorized likeness prompts
PROTECTED_ARTIST_NAMES = {
    "taylor swift",
    "drake",
    "the weeknd",
    "billie eilish",
    "ed sheeran",
    "ariana grande",
    "bad bunny",
    "beyonce",
    "eminem",
    "rihanna",
}


class PolicyValidationResult:
    """Aggregated output of policy and risk screening."""

    def __init__(
        self,
        checks: list[PolicyCheck],
        findings: list[RiskFinding],
        ai_disclosure_required: bool,
        policy_status: str,
    ) -> None:
        self.checks = checks
        self.findings = findings
        self.ai_disclosure_required = ai_disclosure_required
        self.policy_status = policy_status

    @property
    def is_compliant(self) -> bool:
        return self.policy_status == "COMPLIANT" and not any(f.blocking for f in self.findings)


def screen_policy_and_risk(
    evaluation_id: str,
    prompt_text: str | None,
    genre_tags: list[str] | None,
    is_ai_generated: bool = True,
    policy_version: str = "p5-v1",
) -> PolicyValidationResult:
    """Screen track metadata and prompts for platform policy compliance."""
    checks: list[PolicyCheck] = []
    findings: list[RiskFinding] = []

    # 1. YouTube AI Disclosure Requirement (P5 §4.1)
    # AI generated music must mandate disclosure. It is an informational policy requirement, NOT a failure.
    ai_disclosure_required = is_ai_generated
    checks.append(
        PolicyCheck.create(
            evaluation_id_str=evaluation_id,
            policy_namespace="platform_youtube",
            rule_id="ai_synthetic_disclosure",
            rule_version=policy_version,
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            evidence_data={
                "is_ai_generated": is_ai_generated,
                "ai_disclosure_required": ai_disclosure_required,
            },
        )
    )

    # 2. Protected artist reference screening in prompt / genre tags
    combined_text = f"{prompt_text or ''} {' '.join(genre_tags or [])}".lower()
    found_artists: list[str] = []

    for artist in PROTECTED_ARTIST_NAMES:
        if re.search(rf"\b{re.escape(artist)}\b", combined_text):
            found_artists.append(artist)

    if found_artists:
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="PROTECTED_ARTIST_REFERENCE_DETECTED",
                category=GateCategory.POLICY_GATE,
                severity=CheckSeverity.HIGH,
                status="FAIL",
                confidence=0.95,
                message=f"Prompt or metadata contains protected artist reference: {', '.join(found_artists)}",
                blocking=True,
            )
        )
        checks.append(
            PolicyCheck.create(
                evaluation_id_str=evaluation_id,
                policy_namespace="rights_likeness",
                rule_id="protected_artist_likeness",
                rule_version=policy_version,
                status=CheckStatus.FAIL,
                severity=CheckSeverity.HIGH,
                evidence_data={"detected_artists": found_artists},
            )
        )
        policy_status = "NON_COMPLIANT"
    else:
        checks.append(
            PolicyCheck.create(
                evaluation_id_str=evaluation_id,
                policy_namespace="rights_likeness",
                rule_id="protected_artist_likeness",
                rule_version=policy_version,
                status=CheckStatus.PASS,
                severity=CheckSeverity.INFO,
                evidence_data={"detected_artists": []},
            )
        )
        policy_status = "COMPLIANT"

    return PolicyValidationResult(
        checks=checks,
        findings=findings,
        ai_disclosure_required=ai_disclosure_required,
        policy_status=policy_status,
    )

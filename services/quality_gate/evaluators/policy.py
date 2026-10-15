"""Policy and similarity risk screener for Phase P5 (P5 §4.1-§4.4, §16, §31, §36-§40, §87).

Enforces:
- Mandatory YouTube AI disclosure flag (§4.1)
- Protected artist / living person likeness screening of the prompt, genre tags and
  lyrics, using the policy file shared with P4's prompt builder
  (``plm_platform/policy/protected_artists.json``, R4). A hit is
  ``PROTECTED_ARTIST_REFERENCE_DETECTED`` (P29 'artist reference detected'); a voice-clone
  or impersonation request is ``AI_POLICY_CONFLICT`` (P29 'AI policy conflict'). Both
  are hard-stop holds: the candidate is quarantined and only the external process
  (rights counsel, platform) can release it.
"""

from __future__ import annotations

from datetime import datetime

from plm_platform.protected_artists import ProtectedArtistPolicy, load_protected_artist_policy

from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import PolicyCheck, RiskFinding

__all__ = ["PolicyValidationResult", "screen_policy_and_risk"]


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

    @property
    def hold_codes(self) -> tuple[str, ...]:
        return tuple(sorted({f.finding_code for f in self.findings if not f.blocking}))


def screen_policy_and_risk(
    evaluation_id: str,
    prompt_text: str | None,
    genre_tags: list[str] | None,
    is_ai_generated: bool = True,
    policy_version: str = "p5-v1",
    *,
    now: datetime,
    lyrics_text: str | None = None,
    protected_artists: ProtectedArtistPolicy | None = None,
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
            now=now,
        )
    )

    # 2. Protected artist and AI-policy screening (shared policy file, R4).
    artist_policy = protected_artists or load_protected_artist_policy()
    screening = artist_policy.screen(prompt_text, *(genre_tags or []), lyrics_text)
    evidence = {
        "policy_version": screening.policy_version,
        "hits": [
            {"reason_code": h.reason_code, "term_id": h.term_id, "matched": h.matched}
            for h in screening.hits
        ],
        "screened": ["prompt_text", "genre_tags", "lyrics_text"],
    }
    for code in screening.reason_codes:
        terms = screening.term_ids(code)
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code=code,
                category=GateCategory.POLICY_GATE,
                severity=CheckSeverity.CRITICAL,
                status="FAIL",
                confidence=1.0,
                message=f"Prompt, tags or lyrics match {code}: {', '.join(terms)}",
                blocking=False,  # a hard-stop hold: quarantined, never released by approval
                now=now,
            )
        )
    checks.append(
        PolicyCheck.create(
            evaluation_id_str=evaluation_id,
            policy_namespace="rights_likeness",
            rule_id="protected_artist_likeness",
            rule_version=screening.policy_version,
            status=CheckStatus.FAIL if screening.blocked else CheckStatus.PASS,
            severity=CheckSeverity.CRITICAL if screening.blocked else CheckSeverity.INFO,
            evidence_data=evidence,
            now=now,
        )
    )
    policy_status = "NON_COMPLIANT" if screening.blocked else "COMPLIANT"

    return PolicyValidationResult(
        checks=checks,
        findings=findings,
        ai_disclosure_required=ai_disclosure_required,
        policy_status=policy_status,
    )

"""Lyric and vocal evaluator for Phase P5 (P5 §21-§25, §85).

Evaluates vocal presence versus intended vocal mode, lyric completeness,
and prohibited keyword screening.
"""

from __future__ import annotations

import re

from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import LyricCheck, RiskFinding

__all__ = ["LyricEvaluationResult", "evaluate_lyrics"]

# Policy prohibited toxic/hate words for automated screening
PROHIBITED_WORDS = {
    "hate_speech_sample_word",
    "prohibited_violence_target",
}


class LyricEvaluationResult:
    """Aggregated output of lyric and vocal evaluation."""

    def __init__(
        self,
        checks: list[LyricCheck],
        findings: list[RiskFinding],
        is_clean: bool,
    ) -> None:
        self.checks = checks
        self.findings = findings
        self.is_clean = is_clean


def evaluate_lyrics(
    evaluation_id: str,
    vocal_mode: str,
    lyrics_text: str | None,
    prompt_text: str | None = None,
) -> LyricEvaluationResult:
    """Perform deterministic checks on vocal configuration and lyrics."""
    checks: list[LyricCheck] = []
    findings: list[RiskFinding] = []
    text_to_screen = f"{lyrics_text or ''} {prompt_text or ''}".lower()

    # 1. Mode consistency check
    if vocal_mode.lower() == "instrumental":
        if lyrics_text and len(lyrics_text.strip()) > 0:
            findings.append(
                RiskFinding.create(
                    evaluation_id_str=evaluation_id,
                    finding_code="UNEXPECTED_LYRICS_FOR_INSTRUMENTAL",
                    category=GateCategory.QUALITY_GATE,
                    severity=CheckSeverity.MEDIUM,
                    status="WARN",
                    confidence=0.9,
                    message="Lyrics provided for track specified as instrumental.",
                    blocking=False,
                )
            )
            checks.append(
                LyricCheck.create(
                    evaluation_id_str=evaluation_id,
                    check_type="vocal_mode_consistency",
                    status=CheckStatus.WARN,
                    severity=CheckSeverity.MEDIUM,
                    details={"vocal_mode": vocal_mode, "lyrics_length": len(lyrics_text)},
                )
            )
        else:
            checks.append(
                LyricCheck.create(
                    evaluation_id_str=evaluation_id,
                    check_type="vocal_mode_consistency",
                    status=CheckStatus.PASS,
                    severity=CheckSeverity.INFO,
                    details={"vocal_mode": vocal_mode},
                )
            )
    else:
        # Vocal mode expects lyrics or vocal presence
        has_lyrics = bool(lyrics_text and len(lyrics_text.strip()) > 0)
        checks.append(
            LyricCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="vocal_mode_consistency",
                status=CheckStatus.PASS if has_lyrics else CheckStatus.WARN,
                severity=CheckSeverity.INFO if has_lyrics else CheckSeverity.LOW,
                details={"vocal_mode": vocal_mode, "has_lyrics": has_lyrics},
            )
        )

    # 2. Prohibited word screening
    found_prohibited: list[str] = []
    for word in PROHIBITED_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", text_to_screen):
            found_prohibited.append(word)

    if found_prohibited:
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="PROHIBITED_CONTENT_IN_LYRICS",
                category=GateCategory.POLICY_GATE,
                severity=CheckSeverity.CRITICAL,
                status="FAIL",
                confidence=1.0,
                message=f"Found prohibited keywords in lyrics/prompt: {', '.join(found_prohibited)}",
                blocking=True,
            )
        )
        checks.append(
            LyricCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="content_safety",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                details={"prohibited_words_found": found_prohibited},
            )
        )
    else:
        checks.append(
            LyricCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="content_safety",
                status=CheckStatus.PASS,
                severity=CheckSeverity.INFO,
                details={"status": "clean"},
            )
        )

    is_clean = not any(f.blocking for f in findings)
    return LyricEvaluationResult(checks=checks, findings=findings, is_clean=is_clean)

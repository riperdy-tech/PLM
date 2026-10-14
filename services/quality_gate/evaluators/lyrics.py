"""Lyric and vocal evaluator for Phase P5 (P5 §21-§25, §30, §85).

Evaluates vocal presence versus intended vocal mode, lyric completeness, prohibited
keyword screening, and (R4) shingled lyric similarity against the reference corpus:

* MATCH is ``LYRIC_SIMILARITY_HIGH`` (P29 'copyrighted lyric similarity high', hard stop);
* UNKNOWN (no corpus configured or readable, an empty corpus, lyrics too short to judge,
  or a vocal track whose lyric text P5 was not given) is ``LYRIC_SIMILARITY_UNKNOWN``
  (hard stop until the detector can run);
* an instrumental with no lyrics is ``NOT_APPLICABLE``, which is not a hold.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..detectors.evidence import DetectorEvaluation, detector_records
from ..detectors.outcome import DetectorOutcome, DetectorResult
from ..detectors.reason_codes import LYRIC_SIMILARITY_HIGH, LYRIC_SIMILARITY_UNKNOWN
from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import LyricCheck, RiskFinding

__all__ = ["LyricEvaluationResult", "evaluate_lyric_similarity", "evaluate_lyrics"]

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
    *,
    now: datetime,
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
                    now=now,
                )
            )
            checks.append(
                LyricCheck.create(
                    evaluation_id_str=evaluation_id,
                    check_type="vocal_mode_consistency",
                    status=CheckStatus.WARN,
                    severity=CheckSeverity.MEDIUM,
                    details={"vocal_mode": vocal_mode, "lyrics_length": len(lyrics_text)},
                    now=now,
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
                    now=now,
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
                now=now,
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
                now=now,
            )
        )
        checks.append(
            LyricCheck.create(
                evaluation_id_str=evaluation_id,
                check_type="content_safety",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                details={"prohibited_words_found": found_prohibited},
                now=now,
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
                now=now,
            )
        )

    is_clean = not any(f.blocking for f in findings)
    return LyricEvaluationResult(checks=checks, findings=findings, is_clean=is_clean)


def evaluate_lyric_similarity(
    evaluation_id: str, result: DetectorResult, *, now: datetime
) -> DetectorEvaluation:
    """Record the lyric-similarity result and raise a hold for MATCH or UNKNOWN."""
    check, item = detector_records(
        evaluation_id, result, check_type=f"lyric_similarity_{result.source}", now=now
    )
    findings: list[RiskFinding] = []
    if result.outcome is DetectorOutcome.MATCH:
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code=LYRIC_SIMILARITY_HIGH,
                category=GateCategory.RIGHTS_RISK_GATE,
                severity=CheckSeverity.CRITICAL,
                status="FAIL",
                confidence=result.score or 0.0,
                message=(
                    f"Lyrics overlap reference {result.matched_ref!r} (score {result.score}, "
                    f"{result.metrics.get('shared_shingles')} shared shingles)."
                ),
                blocking=False,
                evidence_item_ids=[item.id],
                now=now,
            )
        )
    elif result.outcome is DetectorOutcome.UNKNOWN:
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code=LYRIC_SIMILARITY_UNKNOWN,
                category=GateCategory.RIGHTS_RISK_GATE,
                severity=CheckSeverity.CRITICAL,
                status="FAIL",
                confidence=0.0,
                message=(
                    "Lyric similarity check could not conclude, which is not clearance: "
                    f"{result.reason}"
                ),
                blocking=False,
                evidence_item_ids=[item.id],
                now=now,
            )
        )
    return DetectorEvaluation(
        results=(result,), checks=(check,), findings=tuple(findings), evidence=(item,)
    )

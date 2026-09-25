"""Musical and structural evaluator for Phase P5 (P5 §15-§20, §85).

Calculates dynamic range, estimated integrated LUFS, peak amplitude, and
structural coherence score.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from ..domain.enums import CheckSeverity, GateCategory
from ..domain.models import MusicalCheck, RiskFinding

__all__ = ["MusicalEvaluationResult", "evaluate_musical_qualities"]


class MusicalEvaluationResult:
    """Aggregated output of musical quality evaluation."""

    def __init__(
        self,
        checks: list[MusicalCheck],
        findings: list[RiskFinding],
        composite_score: float,
        confidence: float,
    ) -> None:
        self.checks = checks
        self.findings = findings
        self.composite_score = composite_score
        self.confidence = confidence


def evaluate_musical_qualities(
    evaluation_id: str,
    audio_path: str | Path,
    target_lufs: float = -14.0,
    lufs_tolerance: float = 6.0,
) -> MusicalEvaluationResult:
    """Analyze audio waveform for musical, loudness, and structural attributes."""
    path = Path(audio_path)
    checks: list[MusicalCheck] = []
    findings: list[RiskFinding] = []

    if not path.is_file():
        return MusicalEvaluationResult(
            checks=[],
            findings=[],
            composite_score=0.0,
            confidence=0.0,
        )

    try:
        with wave.open(str(path), "rb") as wf:
            num_frames = wf.getnframes()
            sample_width = wf.getsampwidth()
            frames_data = wf.readframes(min(num_frames, 200000))
    except (wave.Error, OSError):
        return MusicalEvaluationResult(
            checks=[],
            findings=[],
            composite_score=0.0,
            confidence=0.0,
        )

    if sample_width != 2 or not frames_data:
        return MusicalEvaluationResult(
            checks=[],
            findings=[],
            composite_score=0.5,
            confidence=0.3,
        )

    total_samples = len(frames_data) // 2
    samples = [
        struct.unpack_from("<h", frames_data, i * 2)[0]
        for i in range(total_samples)
    ]

    # 1. Peak amplitude and RMS
    max_amp = max(abs(s) for s in samples) if samples else 0
    peak_dbfs = 20 * math.log10(max_amp / 32768.0) if max_amp > 0 else -100.0

    sum_sq = sum(float(s) * float(s) for s in samples)
    rms = math.sqrt(sum_sq / len(samples)) if samples else 0.0
    rms_dbfs = 20 * math.log10(rms / 32768.0) if rms > 0 else -100.0

    # Estimated integrated LUFS approximation ~ RMS dBFS - 3dB
    est_lufs = round(rms_dbfs - 3.0, 1)

    lufs_diff = abs(est_lufs - target_lufs)
    if lufs_diff > lufs_tolerance:
        severity = CheckSeverity.LOW if lufs_diff <= lufs_tolerance + 4 else CheckSeverity.MEDIUM
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="LOUDNESS_DEVIATION",
                category=GateCategory.QUALITY_GATE,
                severity=severity,
                status="WARN",
                confidence=0.8,
                message=f"Estimated loudness {est_lufs} LUFS deviates from target {target_lufs} LUFS by {lufs_diff:.1f} dB",
                blocking=False,
            )
        )
        lufs_severity = CheckSeverity.LOW
    else:
        lufs_severity = CheckSeverity.INFO

    checks.append(
        MusicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="loudness_lufs",
            score=max(0.0, min(1.0, 1.0 - (lufs_diff / 20.0))),
            confidence=0.85,
            severity=lufs_severity,
            method="rms_lufs_estimator",
            segment_data={
                "estimated_lufs": est_lufs,
                "target_lufs": target_lufs,
                "peak_dbfs": round(peak_dbfs, 2),
            },
        )
    )

    # 2. Dynamic range (Crest factor = peak - rms)
    crest_factor = round(peak_dbfs - rms_dbfs, 2)
    dynamic_score = 0.9 if 6.0 <= crest_factor <= 18.0 else 0.6
    checks.append(
        MusicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="dynamic_range",
            score=dynamic_score,
            confidence=0.8,
            severity=CheckSeverity.INFO,
            method="crest_factor",
            segment_data={"crest_factor_db": crest_factor},
        )
    )

    # 3. Structural coherence score (heuristically composite)
    composite_score = round((checks[0].score * 0.6) + (dynamic_score * 0.4), 3)
    confidence = 0.85

    checks.append(
        MusicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="composite_musicality",
            score=composite_score,
            confidence=confidence,
            severity=CheckSeverity.INFO,
            method="heuristic_blender",
        )
    )

    return MusicalEvaluationResult(
        checks=checks,
        findings=findings,
        composite_score=composite_score,
        confidence=confidence,
    )

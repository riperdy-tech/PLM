"""Musical and structural evaluator for Phase P5 (P5 §15, §19, §85; R4).

Judges the measured ITU-R BS.1770 integrated loudness against the declared loudness
target and the crest factor against the declared dynamics band, and blends them into a
composite quality score. Every threshold and weight comes from the audio QA policy
(``quality_gate/audio/audio_qa_policy.json``); its ``policy_version`` is each check's
``model_version``.

Reason codes: ``LOUDNESS_DEVIATION`` (warning) when the measured loudness is outside the
target tolerance; ``MUSICAL_ANALYSIS_UNAVAILABLE`` (blocking, status ``UNKNOWN``) when the
audio cannot be read or decoded or its loudness cannot be measured. An unmeasured file
gets composite score 0 with confidence 0, never a passing score.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..audio.analysis import Unknown, measure_loudness
from ..audio.decode import DecodedAudio, decode_audio
from ..audio.measure import db_from_linear, rms_linear, sample_peak_linear
from ..audio.policy import AudioQaPolicy, load_audio_qa_policy
from ..domain.enums import CheckSeverity, GateCategory
from ..domain.models import MusicalCheck, RiskFinding

__all__ = ["MusicalEvaluationResult", "evaluate_musical_qualities"]

_LOUDNESS_METHOD = "itu_r_bs1770_4_integrated"


class MusicalEvaluationResult:
    """Aggregated output of musical quality evaluation."""

    def __init__(
        self,
        checks: list[MusicalCheck],
        findings: list[RiskFinding],
        composite_score: float,
        confidence: float,
        policy_version: str = "",
    ) -> None:
        self.checks = checks
        self.findings = findings
        self.composite_score = composite_score
        self.confidence = confidence
        self.policy_version = policy_version


def _unavailable(
    evaluation_id: str, policy: AudioQaPolicy, reason: str, *, now: datetime
) -> MusicalEvaluationResult:
    finding = RiskFinding.create(
        evaluation_id_str=evaluation_id,
        finding_code="MUSICAL_ANALYSIS_UNAVAILABLE",
        category=GateCategory.QUALITY_GATE,
        severity=CheckSeverity.HIGH,
        status="UNKNOWN",
        confidence=1.0,
        message=(
            f"Musical analysis could not be made: {reason}. An unmeasured file is not a "
            f"pass. [audio policy {policy.policy_version}]"
        ),
        blocking=True,
        now=now,
    )
    return MusicalEvaluationResult(
        checks=[],
        findings=[finding],
        composite_score=0.0,
        confidence=0.0,
        policy_version=policy.policy_version,
    )


def evaluate_musical_qualities(
    evaluation_id: str,
    audio_path: str | Path,
    target_lufs: float | None = None,
    lufs_tolerance: float | None = None,
    *,
    now: datetime,
    policy: AudioQaPolicy | None = None,
    ffmpeg_path: str | None = None,
) -> MusicalEvaluationResult:
    """Score loudness and dynamics of one audio file against the declared policy.

    ``target_lufs`` and ``lufs_tolerance`` override the policy's loudness profile (for a
    genre profile); by default the policy's values apply.
    """
    policy = policy or load_audio_qa_policy()
    loudness = policy.loudness
    target = loudness.target_lufs if target_lufs is None else target_lufs
    tolerance = loudness.tolerance_lu if lufs_tolerance is None else lufs_tolerance
    scoring = policy.musical_scoring
    path = Path(audio_path)

    if not path.is_file():
        return _unavailable(evaluation_id, policy, f"audio file not found at {path}", now=now)
    decoded = decode_audio(
        path, timeout_seconds=policy.decode.ffmpeg_timeout_seconds, ffmpeg_path=ffmpeg_path
    )
    if not isinstance(decoded, DecodedAudio):
        return _unavailable(
            evaluation_id, policy, f"{decoded.reason_code}: {decoded.detail}", now=now
        )
    lufs = measure_loudness(decoded)
    if isinstance(lufs, Unknown):
        return _unavailable(
            evaluation_id, policy, f"integrated loudness {lufs.reason}: {lufs.detail}", now=now
        )

    checks: list[MusicalCheck] = []
    findings: list[RiskFinding] = []
    peak_dbfs = db_from_linear(sample_peak_linear(decoded.samples))
    rms_dbfs = db_from_linear(rms_linear(decoded.samples))

    # 1. Integrated loudness against the declared target
    lufs_diff = abs(lufs - target)
    if lufs_diff > tolerance:
        severity = (
            CheckSeverity.LOW
            if lufs_diff <= tolerance + loudness.escalation_lu
            else CheckSeverity.MEDIUM
        )
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="LOUDNESS_DEVIATION",
                category=GateCategory.QUALITY_GATE,
                severity=severity,
                status="WARN",
                confidence=1.0,
                message=(
                    f"Integrated loudness {lufs:.1f} LUFS (BS.1770) deviates from the "
                    f"target {target} LUFS by {lufs_diff:.1f} LU (tolerance {tolerance} LU) "
                    f"[audio policy {policy.policy_version}]"
                ),
                blocking=False,
                now=now,
            )
        )
        lufs_severity = CheckSeverity.LOW
    else:
        lufs_severity = CheckSeverity.INFO

    loudness_score = max(0.0, min(1.0, 1.0 - lufs_diff / scoring.loudness_score_span_lu))
    checks.append(
        MusicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="loudness_lufs",
            score=round(loudness_score, 4),
            confidence=scoring.loudness_confidence,
            severity=lufs_severity,
            method=_LOUDNESS_METHOD,
            model_version=policy.policy_version,
            segment_data={
                "integrated_lufs": round(lufs, 2),
                "target_lufs": target,
                "tolerance_lu": tolerance,
                "peak_dbfs": round(peak_dbfs, 2),
            },
            now=now,
        )
    )

    # 2. Dynamic range (crest factor = sample peak - RMS). Loudness is measurable, so the
    # signal carries energy and both levels are finite.
    dynamics = policy.dynamics
    crest_factor = round(peak_dbfs - rms_dbfs, 2)
    in_band = dynamics.crest_factor_min_db <= crest_factor <= dynamics.crest_factor_max_db
    dynamic_score = dynamics.in_range_score if in_band else dynamics.out_of_range_score
    checks.append(
        MusicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="dynamic_range",
            score=dynamic_score,
            confidence=scoring.dynamics_confidence,
            severity=CheckSeverity.INFO,
            method="crest_factor",
            model_version=policy.policy_version,
            segment_data={
                "crest_factor_db": crest_factor,
                "band_db": [dynamics.crest_factor_min_db, dynamics.crest_factor_max_db],
            },
            now=now,
        )
    )

    # 3. Composite (declared weights)
    composite_score = round(
        loudness_score * scoring.loudness_weight + dynamic_score * scoring.dynamics_weight, 3
    )
    checks.append(
        MusicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="composite_musicality",
            score=composite_score,
            confidence=scoring.composite_confidence,
            severity=CheckSeverity.INFO,
            method="weighted_blend",
            model_version=policy.policy_version,
            segment_data={
                "loudness_weight": scoring.loudness_weight,
                "dynamics_weight": scoring.dynamics_weight,
            },
            now=now,
        )
    )

    return MusicalEvaluationResult(
        checks=checks,
        findings=findings,
        composite_score=composite_score,
        confidence=scoring.composite_confidence,
        policy_version=policy.policy_version,
    )

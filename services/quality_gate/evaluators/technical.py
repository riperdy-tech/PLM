"""Technical audio validator for Phase P5 (P5 §11-§15, §84).

Evaluates audio container integrity, format consistency, sample rate, channels,
duration, silence, clipping, and cryptographic SHA-256 integrity.

Duration is judged against the declared range the candidate's C3 spec carries on C4
(``requested_duration_range``, ADR-0032), bounds included. P5 has no tolerance of its
own: inside the range passes; outside is the blocking ``DURATION_OUT_OF_RANGE`` finding.
"""

from __future__ import annotations

import hashlib
import struct
import wave
from datetime import datetime
from pathlib import Path

from plm_contracts.seams.c3 import DurationRangeSeconds

from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import EvidenceItem, RiskFinding, TechnicalCheck

__all__ = ["TechnicalValidationResult", "validate_technical_audio"]


class TechnicalValidationResult:
    """Aggregated output of technical validation."""

    def __init__(
        self,
        checks: list[TechnicalCheck],
        findings: list[RiskFinding],
        evidence: list[EvidenceItem],
        sha256: str,
        measured_duration: float,
        sample_rate: int,
        channels: int,
    ) -> None:
        self.checks = checks
        self.findings = findings
        self.evidence = evidence
        self.sha256 = sha256
        self.measured_duration = measured_duration
        self.sample_rate = sample_rate
        self.channels = channels

    @property
    def has_blocking_defects(self) -> bool:
        return any(f.blocking for f in self.findings)


def validate_technical_audio(
    evaluation_id: str,
    audio_path: str | Path,
    allowed_duration: DurationRangeSeconds,
    expected_sample_rate: int = 44100,
    expected_channels: int = 2,
    *,
    now: datetime,
) -> TechnicalValidationResult:
    """Perform deterministic technical checks on an audio file.

    ``allowed_duration`` is the spec's declared duration range from the C4 event; it is
    required because a duration cannot be judged without one.
    """
    path = Path(audio_path)
    checks: list[TechnicalCheck] = []
    findings: list[RiskFinding] = []
    evidence: list[EvidenceItem] = []

    # 1. File existence and readability
    if not path.is_file():
        finding = RiskFinding.create(
            evaluation_id_str=evaluation_id,
            finding_code="FILE_NOT_FOUND",
            category=GateCategory.TECHNICAL_GATE,
            severity=CheckSeverity.CRITICAL,
            status="FAIL",
            confidence=1.0,
            message=f"Audio file not found at {path}",
            blocking=True,
            now=now,
        )
        check = TechnicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="file_exists",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            measured_value=False,
            expected_value=True,
            unit="boolean",
            now=now,
        )
        return TechnicalValidationResult(
            checks=[check],
            findings=[finding],
            evidence=[],
            sha256="",
            measured_duration=0.0,
            sample_rate=0,
            channels=0,
        )

    # 2. SHA-256 and byte size
    raw_bytes = path.read_bytes()
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    file_size_bytes = len(raw_bytes)

    evidence_item = EvidenceItem.create(
        evaluation_id_str=evaluation_id,
        evidence_type="audio_file_checksum",
        source="file_system",
        source_reference=str(path),
        content_hash=file_sha256,
        summary=f"Audio file size: {file_size_bytes} bytes, sha256: {file_sha256}",
        now=now,
    )
    evidence.append(evidence_item)

    checks.append(
        TechnicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="file_integrity",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            measured_value=file_sha256,
            expected_value="valid_sha256",
            unit="hash",
            now=now,
        )
    )

    # 3. Container & header parsing
    try:
        with wave.open(str(path), "rb") as wf:
            num_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            num_frames = wf.getnframes()
            frames_data = wf.readframes(num_frames)
    except (wave.Error, OSError) as exc:
        finding = RiskFinding.create(
            evaluation_id_str=evaluation_id,
            finding_code="CORRUPT_AUDIO_CONTAINER",
            category=GateCategory.TECHNICAL_GATE,
            severity=CheckSeverity.CRITICAL,
            status="FAIL",
            confidence=1.0,
            message=f"Failed to parse audio container as WAV: {exc}",
            blocking=True,
            evidence_item_ids=[evidence_item.id],
            now=now,
        )
        check = TechnicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="container_valid",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            measured_value="corrupt",
            expected_value="valid_wav",
            unit="container",
            now=now,
        )
        return TechnicalValidationResult(
            checks=checks + [check],
            findings=[finding],
            evidence=evidence,
            sha256=file_sha256,
            measured_duration=0.0,
            sample_rate=0,
            channels=0,
        )

    checks.append(
        TechnicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="container_valid",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            measured_value="wav",
            expected_value="wav",
            unit="container",
            now=now,
        )
    )

    # 4. Sample Rate check
    sr_status = CheckStatus.PASS if framerate == expected_sample_rate else CheckStatus.WARN
    sr_severity = CheckSeverity.INFO if sr_status == CheckStatus.PASS else CheckSeverity.LOW
    checks.append(
        TechnicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="sample_rate",
            status=sr_status,
            severity=sr_severity,
            measured_value=framerate,
            expected_value=expected_sample_rate,
            unit="Hz",
            now=now,
        )
    )

    # 5. Channels check
    ch_status = CheckStatus.PASS if num_channels == expected_channels else CheckStatus.WARN
    ch_severity = CheckSeverity.INFO if ch_status == CheckStatus.PASS else CheckSeverity.LOW
    checks.append(
        TechnicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="channel_count",
            status=ch_status,
            severity=ch_severity,
            measured_value=num_channels,
            expected_value=expected_channels,
            unit="channels",
            now=now,
        )
    )

    # 6. Duration measurement against the declared range (ADR-0032)
    measured_duration = num_frames / float(framerate) if framerate > 0 else 0.0
    expected_range = f"[{allowed_duration.min_seconds}, {allowed_duration.max_seconds}]"
    if allowed_duration.contains(measured_duration):
        dur_status = CheckStatus.PASS
        dur_severity = CheckSeverity.INFO
    else:
        dur_status = CheckStatus.FAIL
        dur_severity = CheckSeverity.HIGH
        findings.append(
            RiskFinding.create(
                evaluation_id_str=evaluation_id,
                finding_code="DURATION_OUT_OF_RANGE",
                category=GateCategory.TECHNICAL_GATE,
                severity=CheckSeverity.HIGH,
                status="FAIL",
                confidence=1.0,
                message=(
                    f"Measured duration {measured_duration:.2f}s lies outside the spec's "
                    f"declared range {expected_range}s"
                ),
                blocking=True,
                evidence_item_ids=[evidence_item.id],
                now=now,
            )
        )

    checks.append(
        TechnicalCheck.create(
            evaluation_id_str=evaluation_id,
            check_type="duration_in_range",
            status=dur_status,
            severity=dur_severity,
            measured_value=round(measured_duration, 3),
            expected_value=expected_range,
            unit="seconds",
            now=now,
        )
    )

    # 7. Sample analysis (clipping & total silence detection)
    if sample_width == 2 and frames_data:
        total_samples = len(frames_data) // 2
        # Unpack up to 100,000 samples for fast deterministic verification
        sample_stride = max(1, total_samples // 100000)
        unpacked_samples = [
            struct.unpack_from("<h", frames_data, i * 2)[0]
            for i in range(0, total_samples, sample_stride)
        ]
        max_amplitude = max(abs(s) for s in unpacked_samples) if unpacked_samples else 0

        # Check total silence
        if max_amplitude == 0:
            findings.append(
                RiskFinding.create(
                    evaluation_id_str=evaluation_id,
                    finding_code="TOTAL_SILENCE_DETECTED",
                    category=GateCategory.TECHNICAL_GATE,
                    severity=CheckSeverity.CRITICAL,
                    status="FAIL",
                    confidence=1.0,
                    message="The audio file contains total digital silence (zero amplitude).",
                    blocking=True,
                    evidence_item_ids=[evidence_item.id],
                    now=now,
                )
            )
            checks.append(
                TechnicalCheck.create(
                    evaluation_id_str=evaluation_id,
                    check_type="silence_detection",
                    status=CheckStatus.FAIL,
                    severity=CheckSeverity.CRITICAL,
                    measured_value=0,
                    expected_value=">0",
                    unit="amplitude",
                    now=now,
                )
            )
        else:
            checks.append(
                TechnicalCheck.create(
                    evaluation_id_str=evaluation_id,
                    check_type="silence_detection",
                    status=CheckStatus.PASS,
                    severity=CheckSeverity.INFO,
                    measured_value=max_amplitude,
                    expected_value=">0",
                    unit="amplitude",
                    now=now,
                )
            )

        # Check severe clipping (full-scale digital clip >= 32760)
        clipping_samples = sum(1 for s in unpacked_samples if abs(s) >= 32760)
        clip_ratio = clipping_samples / len(unpacked_samples) if unpacked_samples else 0.0

        if clip_ratio > 0.05:  # Over 5% clipped samples
            findings.append(
                RiskFinding.create(
                    evaluation_id_str=evaluation_id,
                    finding_code="SEVERE_CLIPPING_DETECTED",
                    category=GateCategory.TECHNICAL_GATE,
                    severity=CheckSeverity.HIGH,
                    status="FAIL",
                    confidence=0.9,
                    message=f"Severe digital clipping detected in {clip_ratio * 100:.1f}% of analyzed samples.",
                    blocking=False,
                    evidence_item_ids=[evidence_item.id],
                    now=now,
                )
            )
            checks.append(
                TechnicalCheck.create(
                    evaluation_id_str=evaluation_id,
                    check_type="clipping_check",
                    status=CheckStatus.WARN,
                    severity=CheckSeverity.MEDIUM,
                    measured_value=round(clip_ratio, 4),
                    expected_value="<0.05",
                    unit="ratio",
                    now=now,
                )
            )
        else:
            checks.append(
                TechnicalCheck.create(
                    evaluation_id_str=evaluation_id,
                    check_type="clipping_check",
                    status=CheckStatus.PASS,
                    severity=CheckSeverity.INFO,
                    measured_value=round(clip_ratio, 4),
                    expected_value="<0.05",
                    unit="ratio",
                    now=now,
                )
            )

    return TechnicalValidationResult(
        checks=checks,
        findings=findings,
        evidence=evidence,
        sha256=file_sha256,
        measured_duration=measured_duration,
        sample_rate=framerate,
        channels=num_channels,
    )

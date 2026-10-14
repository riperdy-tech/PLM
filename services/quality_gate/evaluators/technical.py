"""Technical audio validator for Phase P5 (P5 §11-§18, §84; R4).

The audio is decoded (WAV by libsndfile, anything else by FFmpeg; see
``quality_gate.audio.decode``) and measured (``quality_gate.audio.measure``). Every
threshold comes from the declared, versioned audio QA policy
(``quality_gate/audio/audio_qa_policy.json``); its ``policy_version`` is stamped on each
check (``config_version``), on the ``audio_measurements`` evidence item and on the
result. Each finding carries a reason code and the measured value.

Checks and their reason codes:

* file presence ``FILE_NOT_FOUND`` and decode ``DECODE_FAILED`` / ``DECODER_UNAVAILABLE`` /
  ``DECODE_EMPTY`` (blocking: an undecodable file is never a pass);
* sample rate and channel count against the policy's delivery profile (recorded variance);
* duration against the spec's declared range carried on C4 (``requested_duration_range``,
  ADR-0032), bounds included; outside it is the blocking ``DURATION_OUT_OF_RANGE``;
* BS.1770 integrated loudness (measured here, judged against the loudness target by the
  musical evaluator); a loudness that cannot be measured is ``LOUDNESS_UNMEASURABLE``;
* true peak (4x oversampled) above the ceiling, ``TRUE_PEAK_ABOVE_CEILING`` (warning);
* clipping runs, ``SEVERE_CLIPPING_DETECTED`` (blocking);
* ``TOTAL_SILENCE_DETECTED`` (blocking), ``LEADING_SILENCE_EXCESSIVE`` and
  ``TRAILING_SILENCE_EXCESSIVE`` (warnings);
* stereo phase correlation: ``STEREO_PHASE_INVERTED`` (blocking),
  ``STEREO_NEGATIVE_CORRELATION`` (warning), ``STEREO_CORRELATION_UNMEASURABLE``.

A measurement that cannot be made has check status ``UNKNOWN`` and a blocking finding
with status ``UNKNOWN`` naming the reason. It is never passed.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from plm_contracts.seams.c3 import DurationRangeSeconds

from ..audio.analysis import AudioMeasurements, Unknown, json_number, measure_audio
from ..audio.decode import DecodedAudio, decode_audio
from ..audio.policy import AudioQaPolicy, load_audio_qa_policy
from ..domain.enums import CheckSeverity, CheckStatus, GateCategory
from ..domain.models import EvidenceItem, RiskFinding, TechnicalCheck

__all__ = ["TechnicalValidationResult", "validate_technical_audio"]

_TOOL_NAME = "plm.quality_gate.audio"
_TOOL_VERSION_MAX = 32  # technical_checks.tool_version column width


def _library_version(dist: str) -> str:
    try:
        return f"{dist} {metadata.version(dist)}"[:_TOOL_VERSION_MAX]
    except metadata.PackageNotFoundError:
        return f"{dist} unknown"[:_TOOL_VERSION_MAX]


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
        policy_version: str = "",
        measurements: AudioMeasurements | None = None,
    ) -> None:
        self.checks = checks
        self.findings = findings
        self.evidence = evidence
        self.sha256 = sha256
        self.measured_duration = measured_duration
        self.sample_rate = sample_rate
        self.channels = channels
        self.policy_version = policy_version
        self.measurements = measurements

    @property
    def has_blocking_defects(self) -> bool:
        return any(f.blocking for f in self.findings)


class _Recorder:
    """Builds the checks and findings of one evaluation, stamped with the policy."""

    def __init__(self, evaluation_id: str, policy: AudioQaPolicy, now: datetime) -> None:
        self.evaluation_id = evaluation_id
        self.policy = policy
        self.now = now
        self.checks: list[TechnicalCheck] = []
        self.findings: list[RiskFinding] = []
        self.evidence_ids: list[str] = []

    def check(
        self,
        check_type: str,
        status: CheckStatus,
        severity: CheckSeverity,
        measured: Any,
        expected: Any,
        unit: str,
        *,
        tool_version: str,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
    ) -> None:
        check = TechnicalCheck.create(
            evaluation_id_str=self.evaluation_id,
            check_type=check_type,
            status=status,
            severity=severity,
            measured_value=measured,
            expected_value=expected,
            unit=unit,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            now=self.now,
        )
        self.checks.append(
            dataclasses.replace(
                check,
                tool_name=_TOOL_NAME,
                tool_version=tool_version[:_TOOL_VERSION_MAX],
                config_version=self.policy.policy_version,
            )
        )

    def finding(
        self,
        code: str,
        severity: CheckSeverity,
        message: str,
        *,
        blocking: bool,
        status: str | None = None,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
    ) -> None:
        self.findings.append(
            RiskFinding.create(
                evaluation_id_str=self.evaluation_id,
                finding_code=code,
                category=GateCategory.TECHNICAL_GATE,
                severity=severity,
                status=status or ("FAIL" if blocking else "WARN"),
                confidence=1.0,
                message=f"{message} [audio policy {self.policy.policy_version}]",
                blocking=blocking,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                evidence_item_ids=list(self.evidence_ids),
                now=self.now,
            )
        )

    def unknown(self, check_type: str, code: str, unknown: Unknown, unit: str, tool: str) -> None:
        self.check(
            check_type,
            CheckStatus.UNKNOWN,
            CheckSeverity.HIGH,
            None,
            f"measurable ({unknown.reason})",
            unit,
            tool_version=tool,
        )
        self.finding(
            code,
            CheckSeverity.HIGH,
            f"{check_type} could not be measured ({unknown.reason}): {unknown.detail}. "
            "An unmeasured value is not a pass.",
            blocking=True,
            status="UNKNOWN",
        )


def validate_technical_audio(
    evaluation_id: str,
    audio_path: str | Path,
    allowed_duration: DurationRangeSeconds,
    expected_sample_rate: int | None = None,
    expected_channels: int | None = None,
    *,
    now: datetime,
    policy: AudioQaPolicy | None = None,
    ffmpeg_path: str | None = None,
) -> TechnicalValidationResult:
    """Decode, measure and judge one audio file deterministically.

    ``allowed_duration`` is the spec's declared duration range from the C4 event; it is
    required because a duration cannot be judged without one. ``expected_sample_rate``
    and ``expected_channels`` default to the policy's delivery profile; ``policy``
    defaults to the packaged audio QA policy.
    """
    policy = policy or load_audio_qa_policy()
    rec = _Recorder(evaluation_id, policy, now)
    path = Path(audio_path)

    # 1. File existence
    if not path.is_file():
        rec.finding(
            "FILE_NOT_FOUND",
            CheckSeverity.CRITICAL,
            f"Audio file not found at {path}",
            blocking=True,
        )
        rec.check(
            "file_exists", CheckStatus.FAIL, CheckSeverity.CRITICAL, False, True, "boolean",
            tool_version="filesystem",
        )
        return _result(rec, [], "", 0.0, 0, 0)

    # 2. SHA-256 and byte size of the bytes as stored
    raw_bytes = path.read_bytes()
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    checksum_evidence = EvidenceItem.create(
        evaluation_id_str=evaluation_id,
        evidence_type="audio_file_checksum",
        source="file_system",
        source_reference=str(path)[-128:],
        content_hash=file_sha256,
        summary=f"Audio file size: {len(raw_bytes)} bytes, sha256: {file_sha256}",
        now=now,
    )
    evidence = [checksum_evidence]
    rec.evidence_ids.append(checksum_evidence.id)
    rec.check(
        "file_integrity", CheckStatus.PASS, CheckSeverity.INFO, file_sha256, "valid_sha256",
        "hash", tool_version="hashlib sha256",
    )

    # 3. Decode
    decoded = decode_audio(
        path, timeout_seconds=policy.decode.ffmpeg_timeout_seconds, ffmpeg_path=ffmpeg_path
    )
    if not isinstance(decoded, DecodedAudio):
        rec.finding(
            decoded.reason_code,
            CheckSeverity.CRITICAL,
            f"Audio could not be decoded ({decoded.decoder}): {decoded.detail}",
            blocking=True,
        )
        rec.check(
            "container_valid", CheckStatus.FAIL, CheckSeverity.CRITICAL, decoded.reason_code,
            "decodable_audio", "container", tool_version=decoded.decoder,
        )
        return _result(rec, evidence, file_sha256, 0.0, 0, 0)

    rec.check(
        "container_valid", CheckStatus.PASS, CheckSeverity.INFO,
        f"{decoded.container}/{decoded.codec}", "decodable_audio", "container",
        tool_version=decoded.decoder,
    )

    # 4. Every measurement, recorded once as evidence before it is judged
    measured = measure_audio(decoded, policy)
    measurement_evidence = EvidenceItem.create(
        evaluation_id_str=evaluation_id,
        evidence_type="audio_measurements",
        source=_TOOL_NAME,
        source_reference=file_sha256,
        content_hash=measured.evidence_sha256(),
        summary=measured.evidence_json(),
        now=now,
    )
    evidence.append(measurement_evidence)
    rec.evidence_ids.append(measurement_evidence.id)

    _judge_format(rec, measured, expected_sample_rate, expected_channels, decoded.decoder)
    _judge_duration(rec, measured, allowed_duration, decoded.decoder)
    _judge_loudness_and_peaks(rec, measured)
    _judge_clipping(rec, measured)
    _judge_silence(rec, measured)
    _judge_stereo(rec, measured)

    return _result(
        rec,
        evidence,
        file_sha256,
        measured.duration_seconds,
        measured.sample_rate,
        measured.channels,
        measured,
    )


def _result(
    rec: _Recorder,
    evidence: list[EvidenceItem],
    sha256: str,
    duration: float,
    sample_rate: int,
    channels: int,
    measurements: AudioMeasurements | None = None,
) -> TechnicalValidationResult:
    return TechnicalValidationResult(
        checks=rec.checks,
        findings=rec.findings,
        evidence=evidence,
        sha256=sha256,
        measured_duration=duration,
        sample_rate=sample_rate,
        channels=channels,
        policy_version=rec.policy.policy_version,
        measurements=measurements,
    )


def _judge_format(
    rec: _Recorder,
    m: AudioMeasurements,
    expected_sample_rate: int | None,
    expected_channels: int | None,
    decoder: str,
) -> None:
    rate = expected_sample_rate or rec.policy.format.expected_sample_rate_hz
    channels = expected_channels or rec.policy.format.expected_channels
    for check_type, actual, expected, unit in (
        ("sample_rate", m.sample_rate, rate, "Hz"),
        ("channel_count", m.channels, channels, "channels"),
    ):
        ok = actual == expected
        rec.check(
            check_type,
            CheckStatus.PASS if ok else CheckStatus.WARN,
            CheckSeverity.INFO if ok else CheckSeverity.LOW,
            actual,
            expected,
            unit,
            tool_version=decoder,
        )


def _judge_duration(
    rec: _Recorder, m: AudioMeasurements, allowed: DurationRangeSeconds, decoder: str
) -> None:
    expected_range = f"[{allowed.min_seconds}, {allowed.max_seconds}]"
    inside = allowed.contains(m.duration_seconds)
    if not inside:
        rec.finding(
            "DURATION_OUT_OF_RANGE",
            CheckSeverity.HIGH,
            f"Measured duration {m.duration_seconds:.2f}s lies outside the spec's "
            f"declared range {expected_range}s",
            blocking=True,
        )
    rec.check(
        "duration_in_range",
        CheckStatus.PASS if inside else CheckStatus.FAIL,
        CheckSeverity.INFO if inside else CheckSeverity.HIGH,
        round(m.duration_seconds, 3),
        expected_range,
        "seconds",
        tool_version=decoder,
    )


def _judge_loudness_and_peaks(rec: _Recorder, m: AudioMeasurements) -> None:
    loudness_tool = _library_version("pyloudnorm")
    if isinstance(m.integrated_lufs, Unknown):
        rec.unknown(
            "integrated_loudness", "LOUDNESS_UNMEASURABLE", m.integrated_lufs, "LUFS",
            loudness_tool,
        )
    else:
        rec.check(
            "integrated_loudness", CheckStatus.PASS, CheckSeverity.INFO,
            json_number(m.integrated_lufs, 2), "measured (judged by the musical evaluator)",
            "LUFS", tool_version=loudness_tool,
        )

    numpy_tool = _library_version("numpy")
    rec.check(
        "sample_peak", CheckStatus.PASS, CheckSeverity.INFO, json_number(m.sample_peak_dbfs, 2),
        "measured", "dBFS", tool_version=numpy_tool,
    )

    ceiling = rec.policy.true_peak.ceiling_dbtp
    tp = m.true_peak_dbtp
    if tp > ceiling:
        severity = CheckSeverity.MEDIUM if tp > 0.0 else CheckSeverity.LOW
        rec.finding(
            "TRUE_PEAK_ABOVE_CEILING",
            severity,
            f"True peak {tp:.2f} dBTP ({rec.policy.true_peak.oversampling_factor}x oversampled) "
            f"exceeds the {ceiling} dBTP ceiling; sample peak {m.sample_peak_dbfs:.2f} dBFS",
            blocking=False,
        )
    rec.check(
        "true_peak",
        CheckStatus.WARN if tp > ceiling else CheckStatus.PASS,
        CheckSeverity.INFO if tp <= ceiling else CheckSeverity.LOW,
        json_number(tp, 2),
        f"<= {ceiling}",
        "dBTP",
        tool_version=_library_version("scipy"),
    )


def _judge_clipping(rec: _Recorder, m: AudioMeasurements) -> None:
    policy = rec.policy.clipping
    clip = m.clipping
    blocking = clip.events >= policy.block_min_events
    expected = (
        f"< {policy.block_min_events} runs of >= {policy.min_run_samples} samples "
        f"at >= {policy.full_scale_dbfs} dBFS"
    )
    if blocking:
        rec.finding(
            "SEVERE_CLIPPING_DETECTED",
            CheckSeverity.HIGH,
            f"{clip.events} clipping run(s) of >= {policy.min_run_samples} consecutive "
            f"samples at >= {policy.full_scale_dbfs} dBFS ({clip.clipped_samples} samples; "
            f"longest run {clip.longest_run_samples}; first at "
            f"{clip.first_event_seconds:.3f}s)",
            blocking=True,
            start_seconds=clip.first_event_seconds,
        )
    rec.check(
        "clipping_check",
        CheckStatus.FAIL if blocking else CheckStatus.PASS,
        CheckSeverity.HIGH if blocking else CheckSeverity.INFO,
        clip.events,
        expected,
        "clip_runs",
        tool_version=_library_version("numpy"),
        start_seconds=clip.first_event_seconds,
    )


def _judge_silence(rec: _Recorder, m: AudioMeasurements) -> None:
    policy = rec.policy.silence
    numpy_tool = _library_version("numpy")
    silence = m.silence
    if silence.entirely_silent:
        rec.finding(
            "TOTAL_SILENCE_DETECTED",
            CheckSeverity.CRITICAL,
            f"No frame reaches {policy.threshold_dbfs} dBFS (sample peak "
            f"{m.sample_peak_dbfs} dBFS): the file is silent.",
            blocking=True,
        )
    rec.check(
        "silence_detection",
        CheckStatus.FAIL if silence.entirely_silent else CheckStatus.PASS,
        CheckSeverity.CRITICAL if silence.entirely_silent else CheckSeverity.INFO,
        json_number(m.sample_peak_dbfs, 2),
        f">= {policy.threshold_dbfs}",
        "dBFS",
        tool_version=numpy_tool,
    )
    if silence.entirely_silent:
        return

    for check_type, code, seconds, limit, start, end in (
        (
            "leading_silence", "LEADING_SILENCE_EXCESSIVE", silence.leading_seconds,
            policy.max_leading_seconds, 0.0, silence.leading_seconds,
        ),
        (
            "trailing_silence", "TRAILING_SILENCE_EXCESSIVE", silence.trailing_seconds,
            policy.max_trailing_seconds, m.duration_seconds - silence.trailing_seconds,
            m.duration_seconds,
        ),
    ):
        excessive = seconds > limit
        if excessive:
            rec.finding(
                code,
                CheckSeverity.MEDIUM,
                f"{check_type.replace('_', ' ').capitalize()} of {seconds:.3f}s below "
                f"{policy.threshold_dbfs} dBFS exceeds the {limit}s maximum",
                blocking=False,
                start_seconds=start,
                end_seconds=end,
            )
        rec.check(
            check_type,
            CheckStatus.WARN if excessive else CheckStatus.PASS,
            CheckSeverity.MEDIUM if excessive else CheckSeverity.INFO,
            round(seconds, 3),
            f"<= {limit}",
            "seconds",
            tool_version=numpy_tool,
            start_seconds=start if excessive else None,
            end_seconds=end if excessive else None,
        )


def _judge_stereo(rec: _Recorder, m: AudioMeasurements) -> None:
    policy = rec.policy.stereo
    numpy_tool = _library_version("numpy")
    value = m.stereo_correlation
    if value is None:
        rec.check(
            "stereo_correlation", CheckStatus.SKIPPED, CheckSeverity.INFO, "not_applicable",
            "two channels", "correlation", tool_version=numpy_tool,
        )
        return
    if isinstance(value, Unknown):
        rec.unknown(
            "stereo_correlation", "STEREO_CORRELATION_UNMEASURABLE", value, "correlation",
            numpy_tool,
        )
        return
    expected = f"> {policy.block_below_correlation} (warn < {policy.warn_below_correlation})"
    if value <= policy.block_below_correlation:
        rec.finding(
            "STEREO_PHASE_INVERTED",
            CheckSeverity.HIGH,
            f"Whole-file L/R correlation {value:.3f} is at or below "
            f"{policy.block_below_correlation}: the mono fold-down largely cancels",
            blocking=True,
        )
        status, severity = CheckStatus.FAIL, CheckSeverity.HIGH
    elif value < policy.warn_below_correlation:
        rec.finding(
            "STEREO_NEGATIVE_CORRELATION",
            CheckSeverity.MEDIUM,
            f"Whole-file L/R correlation {value:.3f} is below "
            f"{policy.warn_below_correlation}",
            blocking=False,
        )
        status, severity = CheckStatus.WARN, CheckSeverity.MEDIUM
    else:
        status, severity = CheckStatus.PASS, CheckSeverity.INFO
    rec.check(
        "stereo_correlation", status, severity, round(value, 4), expected, "correlation",
        tool_version=numpy_tool,
    )

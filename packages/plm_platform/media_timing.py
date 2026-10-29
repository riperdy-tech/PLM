"""Fail-closed evidence check for C6 P6 post-render stream timing.

Saved self-reported FFprobe facts are not equivalent to verifying original MP4
bytes. This check protects P7 against legacy READY packages with no stored
stream-timing evidence and obvious contradictory durations.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

__all__ = ["classify_saved_media_timing"]


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def classify_saved_media_timing(
    status: str, container_duration: float, raw_report: str | Mapping[str, Any],
) -> str:
    """Return a bounded reason code; accept only explicit, coherent QA facts."""
    if status != "ready_for_publish":
        return "not_ready"
    try:
        report: Any = json.loads(raw_report) if isinstance(raw_report, str) else raw_report
    except (TypeError, ValueError):
        return "unreadable_report"
    if not isinstance(report, dict):
        return "unreadable_report"
    qa = report.get("qa")
    if (not isinstance(qa, dict) or qa.get("passed") is not True
            or report.get("status") != "passed"
            or qa.get("reason_codes") != []):
        return "not_qa_passed"
    metrics = qa.get("metrics")
    probe = metrics.get("probe") if isinstance(metrics, dict) else None
    streams = probe.get("streams") if isinstance(probe, dict) else None
    if not isinstance(streams, list) or len(streams) != 2:
        return "missing_stream_evidence"

    durations: dict[str, float] = {}
    for stream in streams:
        if not isinstance(stream, dict):
            return "missing_stream_evidence"
        kind = stream.get("codec_type")
        measured = _positive(stream.get("duration_seconds"))
        if kind not in ("video", "audio") or kind in durations or measured is None:
            return "missing_stream_evidence"
        durations[kind] = measured
    if set(durations) != {"video", "audio"}:
        return "missing_stream_evidence"

    profile = report.get("render_profile")
    tolerance = profile.get("duration_tolerance_seconds") if isinstance(profile, dict) else None
    expected = _positive(report.get("timeline_seconds"))
    container = _positive(container_duration)
    measured_container = (_positive(probe.get("duration_seconds"))
                          if isinstance(probe, dict) else None)
    if (expected is None or container is None or measured_container is None
            or isinstance(tolerance, bool) or not isinstance(tolerance, int | float)):
        return "missing_timing_reference"
    tolerance = float(tolerance)
    # No stored report is allowed to broaden the P6 half-second publishing limit.
    if not math.isfinite(tolerance) or not 0 <= tolerance <= 0.5:
        return "missing_timing_reference"
    if (abs(container - measured_container) > tolerance
            or abs(container - expected) > tolerance
            or any(abs(d - expected) > tolerance for d in durations.values())
            or abs(durations["video"] - durations["audio"]) > tolerance):
        return "saved_timing_mismatch"
    return "saved_stream_evidence_consistent"

"""Read-only diagnostic of uploaded YouTube chapter metadata.

YouTube Data API does not confirm that a chapter strip is actually clickable.
This report separately checks the returned description and known album timeline,
then requires a manual watch-page and channel-eligibility check.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .preflight.gates import evaluate_gate_d_chapters, parse_description_chapters

_DURATION = re.compile(
    r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?"
)


def _duration_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    match = _DURATION.fullmatch(value)
    if not match or not any(part is not None for part in match.groupdict().values()):
        return None
    parts = match.groupdict()
    return (
        float(parts["days"] or 0) * 86400
        + float(parts["hours"] or 0) * 3600
        + float(parts["minutes"] or 0) * 60
        + float(parts["seconds"] or 0)
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def inspect_uploaded_video(
    resource: Mapping[str, Any],
    *,
    now: datetime,
    expected_tracks: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Check YouTube's returned metadata, never invent player visibility.

    expected_tracks is the persisted master timeline (title/start_seconds),
    not timestamps inferred from the uploaded description itself.
    """
    video_id = resource.get("id")
    snippet = _mapping(resource.get("snippet"))
    processing = _mapping(resource.get("processingDetails"))
    status = _mapping(resource.get("status"))
    content = _mapping(resource.get("contentDetails"))
    description = snippet.get("description")
    duration = _duration_seconds(content.get("duration"))
    actual = parse_description_chapters(description) if isinstance(description, str) else []

    chapters = [
        {
            "start_seconds": float(seconds), "timestamp_str": (
                f"{seconds // 3600}:{(seconds // 60) % 60:02d}:{seconds % 60:02d}"
                if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"
            ),
            "title": title,
        }
        for seconds, title in actual
    ]
    package = {
        "video": {"duration_seconds": duration or 0},
        "tracklist": list(expected_tracks) if expected_tracks is not None else [],
        "chapters": chapters,
    }
    metadata = {"description": description}
    gate = evaluate_gate_d_chapters(package, metadata, now=now)
    findings = list(gate.diagnostics)
    if duration is None or duration <= 0:
        findings.append("YouTube contentDetails.duration missing or invalid")

    match_expected: bool | None = None
    if expected_tracks is not None:
        expected: list[tuple[int, str]] = []
        valid_source = True
        for i, item in enumerate(expected_tracks):
            start, title = item.get("start_seconds"), item.get("title")
            if (not isinstance(start, (int, float)) or isinstance(start, bool)
                    or not math.isfinite(start) or start < 0
                    or not isinstance(title, str) or not title.strip()):
                findings.append(f"source timeline track {i + 1} invalid")
                valid_source = False
                continue
            expected.append((math.floor(start), title.strip()))
        match_expected = valid_source and actual == expected
        if not match_expected:
            findings.append("published chapter timeline differs from the source album")

    metadata_valid = bool(gate.passed and duration and duration > 0 and match_expected is not False)
    return {
        "video_id": video_id,
        "processing_status": processing.get("processingStatus", "unknown"),
        "privacy_status": status.get("privacyStatus", "unknown"),
        "duration_seconds": duration,
        "chapter_count": len(actual),
        "chapters": [{"start_seconds": s, "title": t} for s, t in actual],
        "matches_source_timeline": match_expected,
        "description_chapters_valid": metadata_valid,
        "findings": findings,
        "player_chapters_visibility": "UNVERIFIED",
        "operator_checks": [
            "Confirm channel Advanced features access and no chapter-ineligible restrictions",
            "Open the published YouTube watch page after processing completes",
            "Confirm chapter segments are visible and clickable, including the final chapter",
        ],
    }

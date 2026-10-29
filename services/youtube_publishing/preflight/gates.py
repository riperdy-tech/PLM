"""Preflight gates A, B, and C for P7 YouTube Publishing Engine (P7 §13).

Every gate takes the caller's ``now``; none reads the wall clock, so a decision replays.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from plm_platform.clock import require_aware

from ..domain.enums import FailureReasonCode
from ..domain.models import ChannelProfile, PreflightCheckResult


def evaluate_gate_a_channel(
    profile: ChannelProfile | None, *, now: datetime
) -> PreflightCheckResult:
    """Gate A: Validate channel configuration, activity, and credential reference (P7 §13)."""
    diagnostics: list[str] = []

    if profile is None:
        diagnostics.append("channel profile not found")
        return PreflightCheckResult(
            passed=False,
            gate_name="Gate A - Channel",
            diagnostics=diagnostics,
            checked_at=now,
        )

    if not profile.active:
        diagnostics.append("channel profile is inactive")

    if not profile.channel_id.strip():
        diagnostics.append("channel_id is empty")

    if not profile.oauth_credential_ref.strip():
        diagnostics.append("oauth_credential_ref is empty")

    return PreflightCheckResult(
        passed=len(diagnostics) == 0,
        gate_name="Gate A - Channel",
        diagnostics=diagnostics,
        checked_at=now,
    )


def evaluate_gate_b_artifact(
    package_payload: dict[str, Any], *, now: datetime,
    selected_thumbnail_id: str | None = None,
) -> PreflightCheckResult:
    """Gate B: Validate video/audio/thumbnail artifacts and checksums (P7 §13)."""
    diagnostics: list[str] = []

    video = package_payload.get("video")
    if not isinstance(video, dict):
        diagnostics.append("missing video artifact payload")
    else:
        if not video.get("master_uri"):
            diagnostics.append("missing video master_uri")
        if not video.get("checksum"):
            diagnostics.append("missing video checksum")
        duration = video.get("duration_seconds", 0.0)
        if duration <= 0:
            diagnostics.append(f"invalid video duration {duration}")

    thumbnails = package_payload.get("thumbnails", [])
    if not isinstance(thumbnails, list) or len(thumbnails) == 0:
        diagnostics.append("missing thumbnail candidates")
    else:
        selected = (
            next((item for item in thumbnails
                  if isinstance(item, dict) and item.get("id") == selected_thumbnail_id), None)
            if selected_thumbnail_id is not None else thumbnails[0]
        )
        if not isinstance(selected, dict) or not selected.get("uri") or not selected.get("checksum"):
            diagnostics.append("invalid or missing selected thumbnail URI/checksum")

    return PreflightCheckResult(
        passed=len(diagnostics) == 0,
        gate_name="Gate B - Artifact",
        diagnostics=diagnostics,
        checked_at=now,
    )


def evaluate_gate_c_metadata(
    metadata: dict[str, Any],
    publish_at: datetime | None = None,
    *,
    now: datetime,
) -> PreflightCheckResult:
    """Gate C: Validate title length, description length, tags limit, category, and scheduling (P7 §13)."""
    diagnostics: list[str] = []

    title = str(metadata.get("title", "")).strip()
    if not title:
        diagnostics.append("title is required and cannot be empty")
    elif len(title) > 100:
        diagnostics.append(f"title length {len(title)} exceeds YouTube 100-character limit")

    desc = str(metadata.get("description", "")).strip()
    if len(desc) > 5000:
        diagnostics.append(f"description length {len(desc)} exceeds YouTube 5000-character limit")

    tags = metadata.get("tags", [])
    if isinstance(tags, list):
        total_tag_len = sum(len(str(t)) for t in tags)
        if total_tag_len > 500:
            diagnostics.append(f"tags total length {total_tag_len} exceeds YouTube 500-character limit")
    else:
        diagnostics.append("tags must be a list of strings")

    cat_id = str(metadata.get("category_id", "10"))
    if cat_id != "10":
        diagnostics.append(f"unsupported category_id {cat_id!r}: expected '10' (Music)")

    now = require_aware(now)
    if publish_at is not None:
        if publish_at.tzinfo is None or publish_at.utcoffset() is None:
            diagnostics.append(f"scheduled publish_at {publish_at} must be timezone-aware")
        elif publish_at <= now:
            diagnostics.append(f"scheduled publish_at {publish_at} must be in the future")

    return PreflightCheckResult(
        passed=len(diagnostics) == 0,
        gate_name="Gate C - Metadata",
        diagnostics=diagnostics,
        checked_at=now,
    )


_CHAPTER_LINE = re.compile(r"^\s*(\d+:\d{2}(?::\d{2})?)\s+(?:[-–—]\s*)?(.+?)\s*$")


def _timestamp_seconds(stamp: str) -> int | None:
    parts = stamp.split(":")
    if len(parts) not in (2, 3):
        return None
    numbers = [int(part) for part in parts]
    if any(n >= 60 for n in numbers[1:]):
        return None
    return numbers[0] * 60 + numbers[1] if len(numbers) == 2 else (
        numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    )


def parse_description_chapters(description: str) -> list[tuple[int, str]]:
    """Extract the player chapter candidates from a video's actual description.

    Each item is (whole-second timestamp, title). Invalid timestamps are ignored,
    so a caller must still validate the count and the full timeline before passing.
    """
    observed: list[tuple[int, str]] = []
    for line in description.splitlines():
        match = _CHAPTER_LINE.fullmatch(line)
        if match:
            parsed = _timestamp_seconds(match.group(1))
            if parsed is not None:
                observed.append((parsed, match.group(2).strip()))
    return observed


def evaluate_gate_d_chapters(
    package_payload: dict[str, Any], metadata: dict[str, Any], *, now: datetime
) -> PreflightCheckResult:
    """Fail closed for long-form albums whose description cannot create player chapters.

    Container-embedded MP4 chapters are not a substitute for YouTube's description
    timestamps. Short single-track videos may have no chapters; any supplied set
    is checked. YouTube account eligibility and player rendering need a separate
    post-publication review.
    """
    video = package_payload.get("video", {})
    duration = video.get("duration_seconds", 0) if isinstance(video, dict) else 0
    tracks = package_payload.get("tracklist", [])
    chapters = package_payload.get("chapters", [])
    required = (
        isinstance(duration, (int, float)) and duration >= 600
    ) or (isinstance(tracks, list) and len(tracks) >= 3)
    diagnostics: list[str] = []
    if not isinstance(chapters, list):
        diagnostics.append("chapters must be a list")
        chapters = []
    if not chapters and not required:
        return PreflightCheckResult(
            passed=True, gate_name="Gate D - Player Chapters", checked_at=now,
        )
    if len(chapters) < 3:
        diagnostics.append("long-form video requires at least 3 chapter timestamps")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        diagnostics.append("chapter validation requires a finite video duration")
        duration = 0

    expected: list[tuple[int, str]] = []
    for i, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            diagnostics.append(f"chapter {i + 1} must be an object")
            continue
        start = chapter.get("start_seconds")
        title = chapter.get("title")
        stamp = chapter.get("timestamp_str")
        if (not isinstance(start, (int, float)) or not math.isfinite(start)
                or start < 0 or start >= duration):
            diagnostics.append(f"chapter {i + 1} has an invalid start")
            continue
        sec = math.floor(start)
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 100:
            diagnostics.append(f"chapter {i + 1} has an invalid title")
            continue
        if not isinstance(stamp, str) or _timestamp_seconds(stamp) != sec:
            diagnostics.append(f"chapter {i + 1} timestamp does not match its start")
        expected.append((sec, title.strip()))

    if expected:
        if expected[0][0] != 0:
            diagnostics.append("first chapter must start at 00:00")
        for i, (sec, _) in enumerate(expected):
            following = expected[i + 1][0] if i + 1 < len(expected) else math.floor(duration)
            if following - sec < 10:
                diagnostics.append(f"chapter {i + 1} must last at least 10 seconds")

    description = metadata.get("description", "")
    observed = parse_description_chapters(description) if isinstance(description, str) else []
    if observed != expected or len(expected) != len(chapters):
        diagnostics.append("description chapter timestamps/titles must exactly match the video timeline")

    return PreflightCheckResult(
        passed=not diagnostics, gate_name="Gate D - Player Chapters",
        diagnostics=diagnostics, checked_at=now,
    )


def evaluate_preflight_all(
    channel_profile: ChannelProfile | None,
    package_payload: dict[str, Any],
    metadata: dict[str, Any],
    publish_at: datetime | None = None,
    *,
    now: datetime,
    selected_thumbnail_id: str | None = None,
) -> tuple[bool, list[PreflightCheckResult], FailureReasonCode | None]:
    """Run all preflight gates. Returns (passed, results, failure_code)."""
    gate_a = evaluate_gate_a_channel(channel_profile, now=now)
    if not gate_a.passed:
        return False, [gate_a], FailureReasonCode.PREFLIGHT_CHANNEL_MISSING

    gate_b = evaluate_gate_b_artifact(
        package_payload, now=now, selected_thumbnail_id=selected_thumbnail_id,
    )
    if not gate_b.passed:
        return False, [gate_a, gate_b], FailureReasonCode.PREFLIGHT_ARTIFACT_MISSING

    gate_c = evaluate_gate_c_metadata(metadata, publish_at, now=now)
    if not gate_c.passed:
        return False, [gate_a, gate_b, gate_c], FailureReasonCode.PREFLIGHT_METADATA_INVALID

    gate_d = evaluate_gate_d_chapters(package_payload, metadata, now=now)
    if not gate_d.passed:
        return False, [gate_a, gate_b, gate_c, gate_d], FailureReasonCode.PREFLIGHT_METADATA_INVALID

    return True, [gate_a, gate_b, gate_c, gate_d], None

"""Preflight gates A, B, and C for P7 YouTube Publishing Engine (P7 §13)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..domain.enums import FailureReasonCode
from ..domain.models import ChannelProfile, PreflightCheckResult


def evaluate_gate_a_channel(profile: ChannelProfile | None) -> PreflightCheckResult:
    """Gate A: Validate channel configuration, activity, and credential reference (P7 §13)."""
    now = datetime.now(UTC)
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


def evaluate_gate_b_artifact(package_payload: dict[str, Any]) -> PreflightCheckResult:
    """Gate B: Validate video/audio/thumbnail artifacts and checksums (P7 §13)."""
    now = datetime.now(UTC)
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
        first_thumb = thumbnails[0]
        if not isinstance(first_thumb, dict) or not first_thumb.get("uri"):
            diagnostics.append("invalid primary thumbnail URI")

    return PreflightCheckResult(
        passed=len(diagnostics) == 0,
        gate_name="Gate B - Artifact",
        diagnostics=diagnostics,
        checked_at=now,
    )


def evaluate_gate_c_metadata(
    metadata: dict[str, Any],
    publish_at: datetime | None = None,
) -> PreflightCheckResult:
    """Gate C: Validate title length, description length, tags limit, category, and scheduling (P7 §13)."""
    now = datetime.now(UTC)
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

    if publish_at is not None and publish_at <= now:
        diagnostics.append(f"scheduled publish_at {publish_at} must be in the future")

    return PreflightCheckResult(
        passed=len(diagnostics) == 0,
        gate_name="Gate C - Metadata",
        diagnostics=diagnostics,
        checked_at=now,
    )


def evaluate_preflight_all(
    channel_profile: ChannelProfile | None,
    package_payload: dict[str, Any],
    metadata: dict[str, Any],
    publish_at: datetime | None = None,
) -> tuple[bool, list[PreflightCheckResult], FailureReasonCode | None]:
    """Run all preflight gates. Returns (passed, results, failure_code)."""
    gate_a = evaluate_gate_a_channel(channel_profile)
    if not gate_a.passed:
        return False, [gate_a], FailureReasonCode.PREFLIGHT_CHANNEL_MISSING

    gate_b = evaluate_gate_b_artifact(package_payload)
    if not gate_b.passed:
        return False, [gate_a, gate_b], FailureReasonCode.PREFLIGHT_ARTIFACT_MISSING

    gate_c = evaluate_gate_c_metadata(metadata, publish_at)
    if not gate_c.passed:
        return False, [gate_a, gate_b, gate_c], FailureReasonCode.PREFLIGHT_METADATA_INVALID

    return True, [gate_a, gate_b, gate_c], None

"""Read-only post-upload checks for real YouTube publications.

An API read cannot prove the player shows clickable chapters. These checks
only prevent P7 from reporting remote-processing/metadata verification
success when the video still lacks the required YouTube description data.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .diagnostics import inspect_uploaded_video
from .domain.models import PublishManifest


@dataclass(frozen=True, slots=True)
class RemoteVideoCheck:
    ready: bool
    retryable: bool
    reason: str


def verify_remote_video(
    resource: Mapping[str, Any],
    *,
    video_id: str,
    manifest: PublishManifest,
    now: datetime,
) -> RemoteVideoCheck:
    """Check the actual uploaded video after YouTube has processed it.

    No real-player visibility inference, no mutation and no invented source
    timeline. A response with pending processing can be retried without upload.
    A changed/missing chapter description needs operator attention.
    """
    if resource.get("id") != video_id:
        return RemoteVideoCheck(False, False, "remote video ID does not match the uploaded ID")
    processing = resource.get("processingDetails")
    processing_status = (
        processing.get("processingStatus") if isinstance(processing, Mapping) else None
    )
    if processing_status in ("failed", "terminated"):
        return RemoteVideoCheck(False, False, f"YouTube processing {processing_status}")
    if processing_status != "succeeded":
        return RemoteVideoCheck(False, True, "YouTube processing is not yet confirmed succeeded")

    status = resource.get("status")
    upload_status = status.get("uploadStatus") if isinstance(status, Mapping) else None
    if upload_status in ("failed", "rejected", "deleted"):
        return RemoteVideoCheck(False, False, f"YouTube upload status {upload_status}")

    snippet = resource.get("snippet")
    description = snippet.get("description") if isinstance(snippet, Mapping) else None
    if not isinstance(description, str) or description != manifest.description:
        return RemoteVideoCheck(
            False, False,
            "YouTube's returned video description differs from the approved publish manifest",
        )

    report = inspect_uploaded_video(resource, now=now)
    duration = report["duration_seconds"]
    if not isinstance(duration, (int, float)) or duration <= 0:
        return RemoteVideoCheck(False, True, "YouTube has not returned a valid video duration")
    if abs(duration - manifest.video_duration_seconds) > max(3., manifest.video_duration_seconds * .01):
        return RemoteVideoCheck(False, False, "YouTube processed duration differs from the media package")
    if not report["description_chapters_valid"]:
        return RemoteVideoCheck(
            False, False,
            "YouTube's returned chapter timestamps are invalid: " + "; ".join(report["findings"]),
        )
    return RemoteVideoCheck(
        True, False, "remote processing and description metadata confirmed; player chapters UNVERIFIED"
    )

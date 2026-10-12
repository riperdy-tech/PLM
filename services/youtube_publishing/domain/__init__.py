"""Domain layer for Phase P7 Autonomous YouTube Publishing Engine."""

from __future__ import annotations

from .enums import FailureReasonCode, OperatingMode, PrivacyStatus, PublishState, RemoteUploadStatus
from .ids import (
    build_attempt_id,
    build_idempotency_key,
    build_manifest_id,
    build_publication_record_id,
    compute_sha256,
)
from .models import (
    ChannelProfile,
    PreflightCheckResult,
    PublishAttempt,
    PublishManifest,
    YouTubePublicationRecord,
    YouTubeVideoPublishedEvent,
)

__all__ = [
    "ChannelProfile",
    "FailureReasonCode",
    "OperatingMode",
    "PreflightCheckResult",
    "PrivacyStatus",
    "PublishAttempt",
    "PublishManifest",
    "PublishState",
    "RemoteUploadStatus",
    "YouTubePublicationRecord",
    "YouTubeVideoPublishedEvent",
    "build_attempt_id",
    "build_idempotency_key",
    "build_manifest_id",
    "build_publication_record_id",
    "compute_sha256",
]

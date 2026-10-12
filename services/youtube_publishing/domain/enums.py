"""Domain enums for Phase P7 Autonomous YouTube Publishing Engine (P7 §8, §12, Ruling R9)."""

from __future__ import annotations

from enum import Enum


class PublishState(str, Enum):
    """Publish attempt states.

    Includes `PROCESSING_TIMEOUT` mandated by Ruling R9.
    """

    RECEIVED = "received"
    LOCKED = "locked"
    PREFLIGHT = "preflight"
    BLOCKED_PREFLIGHT = "blocked_preflight"
    READY = "ready"
    UPLOAD_SESSION_CREATING = "upload_session_creating"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSING_TIMEOUT = "processing_timeout"  # Ruling R9
    METADATA_VERIFYING = "metadata_verifying"
    THUMBNAIL_PENDING = "thumbnail_pending"
    THUMBNAIL_SET = "thumbnail_set"
    PLAYLIST_PENDING = "playlist_pending"
    PLAYLIST_ATTACHED = "playlist_attached"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    VERIFIED = "verified"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"
    QUARANTINED = "quarantined"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        return self in {
            PublishState.VERIFIED,
            PublishState.FAILED_PERMANENT,
            PublishState.QUARANTINED,
            PublishState.CANCELLED,
        }

    def is_active(self) -> bool:
        return not self.is_terminal()


class PrivacyStatus(str, Enum):
    """YouTube video privacy visibility."""

    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class OperatingMode(str, Enum):
    """P7 operational safety modes (P7 §2.1, §65)."""

    DRY_RUN = "dry_run"
    PRIVATE_ONLY = "private_only"
    STAGED = "staged"
    AUTONOMOUS = "autonomous"


class RemoteUploadStatus(str, Enum):
    """YouTube API uploadStatus values."""

    DELETED = "deleted"
    FAILED = "failed"
    PROCESSED = "processed"
    REJECTED = "rejected"
    UPLOADED = "uploaded"


class FailureReasonCode(str, Enum):
    """Standardized failure diagnostic codes."""

    PREFLIGHT_CHANNEL_MISSING = "preflight_channel_missing"
    PREFLIGHT_ARTIFACT_MISSING = "preflight_artifact_missing"
    PREFLIGHT_CHECKSUM_MISMATCH = "preflight_checksum_mismatch"
    PREFLIGHT_METADATA_INVALID = "preflight_metadata_invalid"
    AUTH_TOKEN_EXPIRED = "auth_token_expired"
    QUOTA_EXCEEDED = "quota_exceeded"
    UPLOAD_FAILED = "upload_failed"
    PROCESSING_TIMEOUT_EXCEEDED = "processing_timeout_exceeded"
    POLICY_VIOLATION = "policy_violation"
    REMOTE_NOT_FOUND = "remote_not_found"

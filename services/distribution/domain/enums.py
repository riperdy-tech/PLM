"""Domain enums for Phase P8 DSP / Music Distribution Engine (P8 §6, §7, §8, Ruling R9, R10)."""

from __future__ import annotations

from enum import Enum


class ReleaseType(str, Enum):
    """Release packaging type (P8 §6)."""

    SINGLE = "single"
    EP = "ep"
    ALBUM = "album"
    COMPILATION = "compilation"


class ReleaseStatus(str, Enum):
    """Lifecycle status for a distribution release (P8 §6)."""

    DRAFT = "draft"
    VALIDATING = "validating"
    SUBMISSION_PENDING = "submission_pending"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    PARTIALLY_DELIVERED = "partially_delivered"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    TAKEDOWN_PENDING = "takedown_pending"
    TAKEN_DOWN = "taken_down"
    RESUBMISSION_REQUIRED = "resubmission_required"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        return self in {
            ReleaseStatus.DELIVERED,
            ReleaseStatus.TAKEN_DOWN,
            ReleaseStatus.CANCELLED,
            ReleaseStatus.REJECTED,
        }

    def is_active(self) -> bool:
        return not self.is_terminal()


class DeliveryStatus(str, Enum):
    """Per-store downstream delivery state (P8 §6)."""

    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    FAILED = "failed"
    TAKEN_DOWN = "taken_down"


class StorePlatform(str, Enum):
    """Supported DSP streaming stores and platforms (P8 §4)."""

    SPOTIFY = "spotify"
    APPLE_MUSIC = "apple_music"
    YOUTUBE_MUSIC = "youtube_music"
    AMAZON_MUSIC = "amazon_music"
    TIDAL = "tidal"
    DEEZER = "deezer"


class ISRCSource(str, Enum):
    """Provenance and origin of an ISRC recording code (P8 §7)."""

    INTERNAL_ALLOCATION = "internal_allocation"
    PROVIDER_ASSIGNED = "provider_assigned"
    USER_SUPPLIED = "user_supplied"
    UNKNOWN = "unknown"


class ArtistType(str, Enum):
    """Classification of artist identity (P8 §9)."""

    VIRTUAL = "virtual"
    HUMAN = "human"
    PROJECT = "project"
    BRAND = "brand"

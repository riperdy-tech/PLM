"""Domain enumerations for Phase P6 (Playlist, Visual & Video Factory)."""

from __future__ import annotations

from enum import Enum

__all__ = [
    "PackageStatus",
    "PlaylistStatus",
    "RenderJobStatus",
    "TransitionType",
    "VisualAssetType",
]


class PlaylistStatus(str, Enum):
    """Lifecycle status of a playlist session (P6 §8.1)."""

    PLANNED = "planned"
    VALIDATED = "validated"
    ASSEMBLED = "assembled"
    RENDER_READY = "render_ready"
    RENDERING = "rendering"
    RENDERED = "rendered"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class TransitionType(str, Enum):
    """Transition between tracks in a playlist timeline (P6 §10)."""

    HARD_CUT = "hard_cut"
    SHORT_CROSSFADE = "short_crossfade"
    LONG_CROSSFADE = "long_crossfade"
    SILENCE_GAP = "silence_gap"


class VisualAssetType(str, Enum):
    """Type of visual element composed into the video timeline (P6 §8.4)."""

    BACKGROUND = "background"
    FOREGROUND = "foreground"
    OVERLAY = "overlay"
    LOGO = "logo"
    THUMBNAIL = "thumbnail"
    TEXT_TEXTURE = "text_texture"


class RenderJobStatus(str, Enum):
    """State of an audiovisual render execution (P6 §8.7, §28)."""

    QUEUED = "queued"
    PREFLIGHT = "preflight"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class PackageStatus(str, Enum):
    """Publish readiness of an assembled media package (P6 §5)."""

    DRAFT = "draft"
    READY_FOR_PUBLISH = "ready_for_publish"
    PUBLISHED = "published"
    FAILED = "failed"

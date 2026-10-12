"""YouTube Data API client protocol and abstractions (P7 §4, §5.6)."""

from __future__ import annotations

from typing import Any, Protocol


class YouTubeClientError(Exception):
    """Base exception for YouTube client operations."""


class YouTubeAuthError(YouTubeClientError):
    """Authentication or OAuth token expired/invalid."""


class YouTubeQuotaExceededError(YouTubeClientError):
    """Quota limit exceeded (HTTP 403 quotaExceeded)."""


class YouTubeTransientError(YouTubeClientError):
    """Retryable network or 5xx server error."""


class YouTubeClientProtocol(Protocol):
    """Protocol for YouTube Data API interactions."""

    def insert_resumable_session(
        self,
        *,
        snippet: dict[str, Any],
        status: dict[str, Any],
        content_length: int,
    ) -> str:
        """Initiate resumable upload session. Returns session upload URI."""
        ...

    def upload_video_chunk(
        self,
        *,
        session_uri: str,
        chunk: bytes,
        start_byte: int,
        total_bytes: int,
    ) -> dict[str, Any] | None:
        """Upload chunk. Returns video resource dict when complete, or None if more chunks needed."""
        ...

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        """Retrieve video details (processingDetails, status)."""
        ...

    def set_thumbnail(self, video_id: str, thumbnail_bytes: bytes) -> dict[str, Any]:
        """Upload custom thumbnail for a video."""
        ...

    def insert_playlist(self, title: str, description: str, privacy: str = "private") -> dict[str, Any]:
        """Create a new playlist."""
        ...

    def insert_playlist_item(self, playlist_id: str, video_id: str, position: int = 0) -> dict[str, Any]:
        """Add a video to a playlist."""
        ...

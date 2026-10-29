"""Client package for Phase P7 Autonomous YouTube Publishing Engine."""

from __future__ import annotations

from .base import (
    YouTubeAuthError,
    YouTubeClientError,
    YouTubeClientProtocol,
    YouTubeQuotaExceededError,
    YouTubeTransientError,
)
from .mock import MockYouTubeClient
from .real import RealYouTubeClient

__all__ = [
    "MockYouTubeClient",
    "RealYouTubeClient",
    "YouTubeAuthError",
    "YouTubeClientError",
    "YouTubeClientProtocol",
    "YouTubeQuotaExceededError",
    "YouTubeTransientError",
]

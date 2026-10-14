"""Deterministic mock adapter for YouTube Data API v3 (P7 §4, §26)."""

from __future__ import annotations

import hashlib
from typing import Any

from plm_contracts import ProviderMode

from .base import (
    YouTubeAuthError,
    YouTubeQuotaExceededError,
    YouTubeTransientError,
)


class MockYouTubeClient:
    """In-memory deterministic mock of YouTube Data API v3."""

    provider_mode = ProviderMode.MOCK

    def __init__(
        self,
        *,
        fail_auth: bool = False,
        fail_quota: bool = False,
        transient_failures_remaining: int = 0,
        simulated_video_id: str | None = None,
    ) -> None:
        self.fail_auth = fail_auth
        self.fail_quota = fail_quota
        self.transient_failures_remaining = transient_failures_remaining
        self.simulated_video_id = simulated_video_id

        self.sessions: dict[str, dict[str, Any]] = {}
        self.videos: dict[str, dict[str, Any]] = {}
        self.thumbnails: dict[str, bytes] = {}
        self.playlists: dict[str, dict[str, Any]] = {}
        self.playlist_items: list[dict[str, Any]] = []
        self.recorded_calls: list[dict[str, Any]] = []

    def _check_fault_injection(self, method: str) -> None:
        self.recorded_calls.append({"method": method})
        if self.fail_auth:
            raise YouTubeAuthError("OAuth credential expired or invalid (simulated)")
        if self.fail_quota:
            raise YouTubeQuotaExceededError("YouTube quota exceeded (simulated 403 quotaExceeded)")
        if self.transient_failures_remaining > 0:
            self.transient_failures_remaining -= 1
            raise YouTubeTransientError("Simulated 503 Service Unavailable")

    def insert_resumable_session(
        self,
        *,
        snippet: dict[str, Any],
        status: dict[str, Any],
        content_length: int,
    ) -> str:
        self._check_fault_injection("insert_resumable_session")
        session_id = f"mock_session_{len(self.sessions) + 1}_{hashlib.md5(snippet.get('title', '').encode()).hexdigest()[:8]}"
        session_uri = f"https://mock.upload.youtube.com/upload/youtube/v3/videos?upload_id={session_id}"
        self.sessions[session_uri] = {
            "snippet": snippet,
            "status": status,
            "content_length": content_length,
            "uploaded_bytes": 0,
        }
        return session_uri

    def upload_video_chunk(
        self,
        *,
        session_uri: str,
        chunk: bytes,
        start_byte: int,
        total_bytes: int,
    ) -> dict[str, Any] | None:
        self._check_fault_injection("upload_video_chunk")
        session = self.sessions.get(session_uri)
        if not session:
            session = {
                "snippet": {"title": "Default Video"},
                "status": {"privacyStatus": "private"},
                "content_length": total_bytes,
                "uploaded_bytes": 0,
            }
            self.sessions[session_uri] = session

        session["uploaded_bytes"] = start_byte + len(chunk)

        if session["uploaded_bytes"] >= total_bytes:
            video_id = self.simulated_video_id or f"yt_vid_{hashlib.sha256(session_uri.encode()).hexdigest()[:11]}"
            resource: dict[str, Any] = {
                "id": video_id,
                "snippet": session["snippet"],
                "status": {
                    "uploadStatus": "uploaded",
                    "privacyStatus": session["status"].get("privacyStatus", "private"),
                    "publishAt": session["status"].get("publishAt"),
                    "containsSyntheticMedia": session["status"].get("containsSyntheticMedia", True),
                    "madeForKids": session["status"].get("madeForKids", False),
                },
                "processingDetails": {
                    "processingStatus": "succeeded",
                },
            }
            self.videos[video_id] = resource
            return resource

        return None

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        self._check_fault_injection("get_video")
        return self.videos.get(video_id)

    def set_thumbnail(self, video_id: str, thumbnail_bytes: bytes) -> dict[str, Any]:
        self._check_fault_injection("set_thumbnail")
        self.thumbnails[video_id] = thumbnail_bytes
        thumb_url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
        if video_id in self.videos:
            self.videos[video_id].setdefault("snippet", {})["thumbnails"] = {
                "maxres": {"url": thumb_url}
            }
        return {"items": [{"default": {"url": thumb_url}}]}

    def insert_playlist(self, title: str, description: str, privacy: str = "private") -> dict[str, Any]:
        self._check_fault_injection("insert_playlist")
        pl_id = f"PL_{hashlib.md5(title.encode()).hexdigest()[:16]}"
        resource = {
            "id": pl_id,
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy},
        }
        self.playlists[pl_id] = resource
        return resource

    def insert_playlist_item(self, playlist_id: str, video_id: str, position: int = 0) -> dict[str, Any]:
        self._check_fault_injection("insert_playlist_item")
        item_id = f"PLI_{len(self.playlist_items) + 1}_{video_id}"
        item = {
            "id": item_id,
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
                "position": position,
            },
        }
        self.playlist_items.append(item)
        return item

"""Live real adapter for YouTube Data API v3 (P7 / R6)."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from plm_contracts import ProviderMode
from plm_platform.dotenv import load_env_file

from .base import (
    YouTubeAuthError,
    YouTubeClientError,
    YouTubeQuotaExceededError,
    YouTubeTransientError,
)

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


class RealYouTubeClient:
    """Production client for YouTube Data API v3 using OAuth 2.0 refresh token."""

    provider_mode = ProviderMode.REAL

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        dotenv = load_env_file(Path(".env"))
        if client_id is None:
            client_id = os.getenv("YOUTUBE_OAUTH_CLIENT_ID") or dotenv.get("YOUTUBE_OAUTH_CLIENT_ID", "")
        self.client_id = client_id

        if client_secret is None:
            client_secret = os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET") or dotenv.get("YOUTUBE_OAUTH_CLIENT_SECRET", "")
        self.client_secret = client_secret

        if refresh_token is None:
            refresh_token = os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN") or dotenv.get("YOUTUBE_OAUTH_REFRESH_TOKEN", "")
        self.refresh_token = refresh_token
        self.timeout = timeout_seconds

        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _get_access_token(self) -> str:
        """Fetch or refresh access token using the stored refresh token."""
        if not self.is_configured:
            raise YouTubeAuthError("YouTube OAuth credentials not configured in environment")

        if self._access_token and time.monotonic() < self._access_token_expires_at - 60:
            return self._access_token

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code == 400 or resp.status_code == 401:
                raise YouTubeAuthError(f"OAuth token refresh rejected (HTTP {resp.status_code}): {resp.text[:200]}")
            if resp.status_code != 200:
                raise YouTubeTransientError(f"OAuth token endpoint error (HTTP {resp.status_code}): {resp.text[:200]}")

            data = resp.json()
            token = data.get("access_token")
            if not token or not isinstance(token, str):
                raise YouTubeAuthError("OAuth token response omitted access_token")

            expires_in = float(data.get("expires_in", 3600))
            self._access_token = token
            self._access_token_expires_at = time.monotonic() + expires_in
            return token

    def _auth_headers(self) -> dict[str, str]:
        token = self._get_access_token()
        return {"Authorization": f"Bearer {token}"}

    def _handle_error_response(self, resp: httpx.Response, action: str) -> None:
        if resp.status_code in (401, 403):
            text = resp.text
            if "quotaExceeded" in text or "rateLimitExceeded" in text:
                raise YouTubeQuotaExceededError(f"YouTube quota exceeded during {action}: {text[:200]}")
            if resp.status_code == 401 or "invalid_grant" in text:
                raise YouTubeAuthError(f"YouTube authentication failed during {action}: {text[:200]}")
            raise YouTubeClientError(f"YouTube permission error during {action} (HTTP {resp.status_code}): {text[:200]}")
        if resp.status_code >= 500:
            raise YouTubeTransientError(f"YouTube server error during {action} (HTTP {resp.status_code}): {resp.text[:200]}")
        raise YouTubeClientError(f"YouTube API call {action} failed (HTTP {resp.status_code}): {resp.text[:200]}")

    def insert_resumable_session(
        self,
        *,
        snippet: dict[str, Any],
        status: dict[str, Any],
        content_length: int,
        upload_key: str | None = None,
    ) -> str:
        """Initiate resumable upload session. Returns session upload URI."""
        url = f"{YOUTUBE_UPLOAD_URL}?uploadType=resumable&part=snippet,status"
        headers = self._auth_headers()
        headers.update({
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(content_length),
            "X-Upload-Content-Type": "video/mp4",
        })
        payload = {"snippet": snippet, "status": status}

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                self._handle_error_response(resp, "insert_resumable_session")

            location = resp.headers.get("Location")
            if not location:
                raise YouTubeClientError("YouTube resumable upload session omitted Location header")
            return str(location)

    def upload_video_chunk(
        self,
        *,
        session_uri: str,
        chunk: bytes,
        start_byte: int,
        total_bytes: int,
    ) -> dict[str, Any] | None:
        """Upload chunk. Returns video resource dict when complete, or None if more chunks needed."""
        end_byte = start_byte + len(chunk) - 1
        headers = {
            "Content-Range": f"bytes {start_byte}-{end_byte}/{total_bytes}",
            "Content-Type": "video/mp4",
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.put(session_uri, headers=headers, content=chunk)
            if resp.status_code == 308:
                # Resume Incomplete: more chunks needed
                return None
            if resp.status_code in (200, 201):
                data = resp.json()
                if isinstance(data, dict):
                    return data
                return {}
            self._handle_error_response(resp, "upload_video_chunk")
            return None

    def list_recent_uploads(
        self, *, max_results: int = 20, channel_id: str | None = None
    ) -> list[dict[str, str]]:
        """Read only the authenticated channel's recent uploads, newest first.

        Uses the upload playlist instead of search.list (lower quota cost and
        no incidental results from another channel). No shared API key needed.
        """
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be between 1 and 50")
        if channel_id is not None and not channel_id.strip():
            raise ValueError("channel_id cannot be empty")
        with httpx.Client(timeout=self.timeout) as client:
            channels = client.get(
                f"{YOUTUBE_API_BASE}/channels",
                headers=self._auth_headers(),
                params={"part": "id,contentDetails", "mine": "true"},
            )
            if channels.status_code != 200:
                self._handle_error_response(channels, "list_recent_uploads.channels")
            payload = channels.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise YouTubeClientError("channels.list returned malformed channel data")
            records = payload["items"]
            if len(records) != 1 or not isinstance(records[0], dict):
                raise YouTubeClientError("expected exactly one authenticated YouTube channel")
            record = records[0]
            actual_id = record.get("id")
            if not isinstance(actual_id, str) or not actual_id:
                raise YouTubeClientError("authenticated channel ID is missing")
            if channel_id is not None and actual_id != channel_id:
                raise YouTubeClientError("authenticated YouTube channel does not match requested channel")
            details = record.get("contentDetails")
            related = details.get("relatedPlaylists") if isinstance(details, dict) else None
            uploads_id = related.get("uploads") if isinstance(related, dict) else None
            if not isinstance(uploads_id, str) or not uploads_id:
                raise YouTubeClientError("channel uploads playlist is unavailable")
            response = client.get(
                f"{YOUTUBE_API_BASE}/playlistItems",
                headers=self._auth_headers(),
                params={
                    "part": "snippet,contentDetails",
                    "playlistId": uploads_id,
                    "maxResults": max_results,
                },
            )
            if response.status_code != 200:
                self._handle_error_response(response, "list_recent_uploads.playlistItems")
            data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise YouTubeClientError("playlistItems.list returned malformed upload data")
        uploads: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in data["items"]:
            if not isinstance(item, dict):
                raise YouTubeClientError("upload playlist contains malformed item")
            snippet = item.get("snippet")
            content = item.get("contentDetails")
            if not isinstance(snippet, dict):
                raise YouTubeClientError("upload playlist item has no snippet")
            resource = snippet.get("resourceId")
            video_id = resource.get("videoId") if isinstance(resource, dict) else None
            if not isinstance(video_id, str) or not video_id:
                video_id = content.get("videoId") if isinstance(content, dict) else None
            title = snippet.get("title")
            published_at = (
                content.get("videoPublishedAt") if isinstance(content, dict) else None
            ) or snippet.get("publishedAt")
            if (not isinstance(video_id, str) or not video_id
                    or not isinstance(title, str) or not title.strip()
                    or not isinstance(published_at, str) or not published_at):
                raise YouTubeClientError("upload playlist item is missing identity/metadata")
            if video_id in seen:
                continue
            uploads.append({"video_id": video_id, "title": title, "published_at": published_at})
            seen.add(video_id)
        return uploads

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        """Retrieve video details for processing and read-only post-publish QA."""
        url = f"{YOUTUBE_API_BASE}/videos"
        params = {
            "id": video_id,
            "part": "snippet,status,processingDetails,contentDetails",
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self._auth_headers(), params=params)
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                self._handle_error_response(resp, "get_video")
            data = resp.json()
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items or not isinstance(items[0], dict):
                return None
            item: dict[str, Any] = items[0]
            return item

    def set_thumbnail(self, video_id: str, thumbnail_bytes: bytes) -> dict[str, Any]:
        """Upload custom thumbnail for a video."""
        url = f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}"
        # P6 emits PNG, while some operator-supplied assets are JPEG. Declaring
        # JPEG for PNG bytes can lead to rejected or incorrectly processed covers.
        if thumbnail_bytes.startswith(bytes.fromhex("89504e470d0a1a0a")):
            content_type = "image/png"
        elif thumbnail_bytes.startswith(bytes.fromhex("ffd8ff")):
            content_type = "image/jpeg"
        else:
            raise YouTubeClientError("thumbnail is not a PNG or JPEG image")
        headers = self._auth_headers()
        headers["Content-Type"] = content_type

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, content=thumbnail_bytes)
            if resp.status_code != 200:
                self._handle_error_response(resp, "set_thumbnail")
            data = resp.json()
            return data if isinstance(data, dict) else {}

    def insert_playlist(self, title: str, description: str, privacy: str = "private") -> dict[str, Any]:
        """Create a new playlist."""
        url = f"{YOUTUBE_API_BASE}/playlists?part=snippet,status"
        payload = {
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy},
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self._auth_headers(), json=payload)
            if resp.status_code not in (200, 201):
                self._handle_error_response(resp, "insert_playlist")
            data = resp.json()
            return data if isinstance(data, dict) else {}

    def insert_playlist_item(self, playlist_id: str, video_id: str, position: int = 0) -> dict[str, Any]:
        """Add a video to a playlist."""
        url = f"{YOUTUBE_API_BASE}/playlistItems?part=snippet"
        payload = {
            "snippet": {
                "playlistId": playlist_id,
                "position": position,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self._auth_headers(), json=payload)
            if resp.status_code not in (200, 201):
                self._handle_error_response(resp, "insert_playlist_item")
            data = resp.json()
            return data if isinstance(data, dict) else {}

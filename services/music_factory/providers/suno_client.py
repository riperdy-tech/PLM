"""Suno AI Music Generation REST client for Phase P4 (R6)."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx
from plm_platform.clock import Clock
from plm_platform.dotenv import load_env_file

logger = logging.getLogger(__name__)

SUNO_API_BASE: Final[str] = "https://studio-api-prod.suno.com/api"
DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


class SunoClientError(RuntimeError):
    """Raised when communication with Suno API fails."""


@dataclass(frozen=True, slots=True)
class SunoClip:
    """Metadata of a generated Suno audio clip."""

    clip_id: str
    status: str
    title: str
    audio_url: str | None
    video_url: str | None
    image_url: str | None
    duration_seconds: float | None
    model_name: str
    tags: str
    error_message: str | None = None


class SunoClient:
    """Client for Suno's studio API using an authenticated session token."""

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = SUNO_API_BASE,
        timeout_seconds: float = 30.0,
        clock: Clock | None = None,
    ) -> None:

        if token is None:
            dotenv = load_env_file(Path(".env"))
            raw_token = (
                os.getenv("SUNO_API_KEY")
                or os.getenv("MUSIC_PROVIDER_API_KEY")
                or dotenv.get("SUNO_API_KEY", "")
                or dotenv.get("MUSIC_PROVIDER_API_KEY", "")
            )
        else:
            raw_token = token
        # Strip optional "Bearer " prefix if present
        self._token = raw_token.removeprefix("Bearer ").strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._clock = clock
        self._token_expires_at: float | None = None
        self._compute_token_expiry()

    @property
    def is_configured(self) -> bool:
        return bool(self._token)

    def _compute_token_expiry(self) -> None:
        if not self._token:
            self._token_expires_at = None
            return
        try:
            parts = self._token.split(".")
            if len(parts) >= 2:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
                exp = float(payload.get("exp", 0))
                iat = float(payload.get("iat", 0))
                ttl = (exp - iat) if (exp > 0 and iat > 0 and exp > iat) else 3600.0
                self._token_expires_at = time.monotonic() + ttl
        except (ValueError, json.JSONDecodeError):
            self._token_expires_at = None

    def _is_token_expired(self) -> bool:
        if not self._token:
            return True
        if self._clock is not None:
            try:
                parts = self._token.split(".")
                if len(parts) >= 2:
                    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
                    exp = float(payload.get("exp", 0))
                    return self._clock.now().timestamp() > (exp - 60)
            except (ValueError, json.JSONDecodeError):
                return False
        if self._token_expires_at is not None:
            return time.monotonic() >= self._token_expires_at - 60
        return False


    def refresh_session_token(self, cookie: str | None = None) -> str:
        """Refresh JWT using Clerk session cookie."""
        dotenv = load_env_file(Path(".env"))
        client_cookie = cookie or os.getenv("SUNO_COOKIE") or dotenv.get("SUNO_COOKIE", "")
        if not client_cookie:
            raise SunoClientError("No SUNO_COOKIE available for token refresh")

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Origin": "https://suno.com",
            "Cookie": client_cookie if "=" in client_cookie else f"__client={client_cookie}",
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get("https://auth.suno.com/v1/client", headers=headers)
            if resp.status_code != 200:
                raise SunoClientError(f"Clerk client fetch failed (HTTP {resp.status_code}): {resp.text[:200]}")
            data = resp.json().get("response", {})
            sessions = data.get("sessions", [])
            if not sessions:
                raise SunoClientError("No active Clerk sessions found for cookie")

            active_session = sessions[0]
            session_id = active_session["id"]
            token_resp = client.post(
                f"https://auth.suno.com/v1/client/sessions/{session_id}/tokens",
                headers=headers,
            )
            if token_resp.status_code == 200:
                jwt = token_resp.json().get("jwt")
                if jwt and isinstance(jwt, str):
                    self._token = jwt
                    self._compute_token_expiry()
                    return jwt

            last_token = active_session.get("last_active_token", {}).get("jwt")
            if last_token and isinstance(last_token, str):
                self._token = last_token
                self._compute_token_expiry()
                return last_token

            raise SunoClientError("Failed to extract JWT from Clerk session response")

    def _headers(self) -> dict[str, str]:
        if self._is_token_expired():
            try:
                self.refresh_session_token()
            except (SunoClientError, httpx.HTTPError) as e:
                logger.debug("Auto token refresh skipped/failed: %s", e)

        if not self._token:
            raise SunoClientError("Suno API token is not configured")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://suno.com",
            "Referer": "https://suno.com/",
        }
        # If the token contains cookies (e.g. key=value;), pass it as Cookie too
        if "=" in self._token:
            headers["Cookie"] = self._token
        return headers

    def get_billing_info(self) -> dict[str, Any]:
        """Fetch billing / credit quota info from Suno."""
        url = f"{self._base_url}/billing/info/"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers())
            if resp.status_code != 200:
                raise SunoClientError(
                    f"Suno billing check failed (HTTP {resp.status_code}): {resp.text[:200]}"
                )
            data = resp.json()
            if isinstance(data, dict):
                return data
            return {}

    def check_health(self) -> bool:
        """Check if Suno API is reachable and token is valid."""
        if not self.is_configured:
            return False
        try:
            info = self.get_billing_info()
            return "total_credits_left" in info or "credits_left" in info
        except (SunoClientError, httpx.HTTPError) as exc:
            logger.warning("Suno health check failed: %s", exc)
            return False

    def submit_generation(
        self,
        *,
        prompt: str,
        tags: str,
        title: str,
        instrumental: bool = True,
        model: str = "chirp-v3-5",
    ) -> tuple[str, ...]:
        """Submit a generation request to Suno. Returns tuple of clip IDs."""
        url = f"{self._base_url}/generate/v2/"
        payload = {
            "prompt": "" if instrumental else prompt,
            "tags": tags,
            "title": title,
            "make_instrumental": instrumental,
            "model": model,
            "wait_audio": False,
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
            if resp.status_code not in (200, 201):
                raise SunoClientError(
                    f"Suno generation submission failed (HTTP {resp.status_code}): {resp.text[:200]}"
                )
            data = resp.json()
            clips = data.get("clips", [])
            clip_ids = tuple(str(c["id"]) for c in clips if "id" in c)
            if not clip_ids and "id" in data:
                clip_ids = (str(data["id"]),)
            return clip_ids

    def poll_clip(
        self,
        clip_id: str,
        *,
        max_wait_seconds: float = 300.0,
        poll_interval: float = 5.0,
    ) -> SunoClip:
        """Poll until a specific Suno clip completes generation or fails."""
        url = f"{self._base_url}/feed/?ids={clip_id}"
        deadline = time.monotonic() + max_wait_seconds

        with httpx.Client(timeout=self._timeout) as client:
            while time.monotonic() < deadline:
                resp = client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    clips = data if isinstance(data, list) else data.get("clips", [data])
                    for raw in clips:
                        if str(raw.get("id")) == clip_id:
                            status = raw.get("status", "pending")
                            if status == "complete":
                                return SunoClip(
                                    clip_id=clip_id,
                                    status="complete",
                                    title=raw.get("title", ""),
                                    audio_url=raw.get("audio_url"),
                                    video_url=raw.get("video_url"),
                                    image_url=raw.get("image_url"),
                                    duration_seconds=float(raw.get("duration", 0.0)) or None,
                                    model_name=raw.get("model_name", "chirp-v3-5"),
                                    tags=raw.get("tags", ""),
                                )
                            if status in ("error", "failed"):
                                error_msg = raw.get("error_message") or raw.get("metadata", {}).get("error_message")
                                raise SunoClientError(f"Suno clip {clip_id} failed: {error_msg}")
                time.sleep(poll_interval)

        raise SunoClientError(f"Timed out waiting for Suno clip {clip_id} after {max_wait_seconds}s")

    def extract_audio_via_video(
        self,
        clip_id: str,
        *,
        max_wait_seconds: float = 120.0,
        poll_interval: float = 3.0,
    ) -> tuple[bytes, float]:
        """Generate/poll song video and demux pristine audio into 44.1kHz stereo WAV.

        This route uses Suno's video streaming pipeline without consuming the monthly
        download allowance (P4 / R6 quota preservation).
        """
        video_url: str | None = None
        with httpx.Client(timeout=self._timeout) as client:
            # 1. Check existing clip for video_url
            feed_resp = client.get(f"{self._base_url}/feed/?ids={clip_id}", headers=self._headers())
            if feed_resp.status_code == 200:
                feed_data = feed_resp.json()
                clip_data = feed_data[0] if isinstance(feed_data, list) and feed_data else {}
                url = clip_data.get("video_url")
                if url and isinstance(url, str) and url.strip():
                    video_url = url.strip()

            # 2. If no video_url, trigger video generation (handling 429 concurrency limits)
            if not video_url:
                trigger_deadline = time.monotonic() + 90.0
                while time.monotonic() < trigger_deadline:
                    gen_resp = client.post(
                        f"{self._base_url}/video/generate/{clip_id}/",
                        headers=self._headers(),
                        json={},
                    )
                    if gen_resp.status_code in (200, 201, 202, 204):
                        break
                    if gen_resp.status_code == 429:
                        logger.debug("Suno video generator busy (HTTP 429); waiting 6s for slot...")
                        time.sleep(6.0)
                        continue
                    logger.warning(
                        "Video generation trigger returned HTTP %d: %s",
                        gen_resp.status_code,
                        gen_resp.text[:100],
                    )
                    break


                # Poll until video_url appears
                deadline = time.monotonic() + max_wait_seconds
                while time.monotonic() < deadline:
                    r = client.get(f"{self._base_url}/feed/?ids={clip_id}", headers=self._headers())
                    if r.status_code == 200:
                        data = r.json()
                        item = data[0] if isinstance(data, list) and data else {}
                        url = item.get("video_url")
                        if url and isinstance(url, str) and url.strip():
                            video_url = url.strip()
                            break
                    time.sleep(poll_interval)

            if not video_url:
                raise SunoClientError(
                    f"Timed out waiting for video generation for clip {clip_id}"
                )

            # 3. Download MP4 stream from CDN
            resp = client.get(video_url)
            if resp.status_code != 200:
                raise SunoClientError(f"Failed to fetch video stream from {video_url}: HTTP {resp.status_code}")
            video_bytes = resp.content

        # 4. Extract audio directly into standard 44.1 kHz, 16-bit stereo PCM WAV via FFmpeg
        with tempfile.TemporaryDirectory() as tmpdir:
            src_file = os.path.join(tmpdir, "suno_video.mp4")
            dst_file = os.path.join(tmpdir, "extracted_44k.wav")

            with open(src_file, "wb") as f:
                f.write(video_bytes)

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                src_file,
                "-vn",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                dst_file,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                raise SunoClientError(f"FFmpeg audio extraction failed: {res.stderr[:300]}")

            with open(dst_file, "rb") as f:
                wav_bytes = f.read()

            import wave

            with wave.open(dst_file, "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                duration = frames / float(rate)

            return wav_bytes, duration

    def download_clip_wav(
        self,
        clip_id: str,
        *,
        prefer_video_demux: bool = True,
        max_wait_seconds: float = 60.0,
        poll_interval: float = 2.0,
    ) -> tuple[bytes, float]:
        """Authorize and download high-fidelity WAV for clip, returning 44.1kHz stereo PCM WAV.

        If prefer_video_demux=True (default), demuxes the song video without consuming
        the user's monthly download allowance. If False, executes official download authorization.
        """
        if prefer_video_demux:
            try:
                return self.extract_audio_via_video(
                    clip_id,
                    max_wait_seconds=max_wait_seconds,
                    poll_interval=poll_interval,
                )
            except (SunoClientError, httpx.HTTPError, subprocess.SubprocessError) as exc:
                logger.warning(
                    "Video audio extraction failed for clip %s (%s); falling back to official download",
                    clip_id,
                    exc,
                )

        with httpx.Client(timeout=self._timeout) as client:
            # 1. Check if download is already unlocked; if not, authorize it
            feed_resp = client.get(f"{self._base_url}/feed/?ids={clip_id}", headers=self._headers())
            if feed_resp.status_code == 200:
                feed_data = feed_resp.json()
                clip_data = feed_data[0] if isinstance(feed_data, list) and feed_data else {}
                if not clip_data.get("is_download_unlocked"):
                    auth_resp = client.post(
                        f"{self._base_url}/download/authorize",
                        headers=self._headers(),
                        json={"item_id": clip_id, "item_type": "clip"},
                    )
                    if auth_resp.status_code not in (200, 201):
                        logger.warning(
                            "Download authorization returned HTTP %d: %s",
                            auth_resp.status_code,
                            auth_resp.text[:100],
                        )

            # 2. Poll prepared download URL
            deadline = time.monotonic() + max_wait_seconds
            download_url: str | None = None
            while time.monotonic() < deadline:
                r = client.get(
                    f"{self._base_url}/download/clip/{clip_id}?format=wav",
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "ready" and data.get("download_url"):
                        download_url = data["download_url"]
                        break
                time.sleep(poll_interval)

            if not download_url:
                raise SunoClientError(
                    f"Timed out waiting for prepared WAV download URL for clip {clip_id}"
                )

            # 3. Fetch from signed S3 URL
            resp = client.get(download_url)
            if resp.status_code != 200:
                raise SunoClientError(f"Failed to fetch WAV from S3: HTTP {resp.status_code}")
            raw_bytes = resp.content

        # 4. Transcode to standard 44.1 kHz, 16-bit stereo PCM WAV via FFmpeg
        with tempfile.TemporaryDirectory() as tmpdir:
            src_file = os.path.join(tmpdir, "suno_raw.wav")
            dst_file = os.path.join(tmpdir, "output_44k.wav")

            with open(src_file, "wb") as f:
                f.write(raw_bytes)

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                src_file,
                "-ar",
                "44100",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                dst_file,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                raise SunoClientError(f"FFmpeg conversion failed: {res.stderr[:300]}")

            with open(dst_file, "rb") as f:
                wav_bytes = f.read()

            import wave

            with wave.open(dst_file, "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                duration = frames / float(rate)

            return wav_bytes, duration

    @staticmethod
    def download_and_convert_to_wav(audio_url: str) -> tuple[bytes, float]:
        """Download remote audio file and convert to 44.1 kHz, 16-bit stereo PCM WAV via FFmpeg."""
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(audio_url)
            if resp.status_code != 200:
                raise SunoClientError(f"Failed to download audio from {audio_url}: HTTP {resp.status_code}")
            src_bytes = resp.content

        with tempfile.TemporaryDirectory() as tmpdir:
            src_file = os.path.join(tmpdir, "suno_input.mp3")
            dst_file = os.path.join(tmpdir, "output.wav")

            with open(src_file, "wb") as f:
                f.write(src_bytes)

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                src_file,
                "-ar",
                "44100",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                dst_file,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                raise SunoClientError(f"FFmpeg conversion failed: {res.stderr[:300]}")

            with open(dst_file, "rb") as f:
                wav_bytes = f.read()

            # Probe duration using wave
            import wave

            with wave.open(dst_file, "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                duration = frames / float(rate)

            return wav_bytes, duration


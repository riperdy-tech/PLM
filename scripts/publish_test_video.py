"""Test publish of rendered video master directly to Rhodes & Rain Records channel."""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.youtube_publishing.client.base import YouTubeClientError
from services.youtube_publishing.client.real import RealYouTubeClient

VIDEO_PATH = Path("output_media/suno_real_track_preview.mp4")
THUMBNAIL_PATH = Path("output_media/suno_artwork.jpeg")

TITLE = "Midnight Rain & Rhodes - Lofi Chill Beats"
DESCRIPTION = """Relaxing lofi beats featuring warm Rhodes piano chords and gentle rain sounds.
Produced by Rhodes & Rain Records.

Track: Midnight Rain & Rhodes
Duration: 03:26
Key: D minor
Tempo: 80 BPM
Genre: Lo-Fi / Chillhop / Ambient

Artwork & Music created autonomously with PLM.
#lofi #chillbeats #rhodes #rain #lofirain #studybeats #ambient
"""
TAGS = [
    "lofi",
    "lofi hip hop",
    "chill beats",
    "rhodes piano",
    "rain sounds",
    "relaxing beats",
    "study music",
    "rhodes and rain records",
]

CHUNK_SIZE = 1024 * 1024  # 1MB chunk size for upload


def main() -> None:
    if not VIDEO_PATH.is_file():
        print(f"Error: Video file {VIDEO_PATH} not found.")
        sys.exit(1)

    video_bytes = VIDEO_PATH.read_bytes()
    total_bytes = len(video_bytes)
    print(f"Loaded video: {VIDEO_PATH} ({total_bytes:,} bytes / {total_bytes / (1024 * 1024):.2f} MB)")

    client = RealYouTubeClient()
    if not client.is_configured:
        print("Error: YouTube OAuth credentials missing from environment / .env")
        sys.exit(1)

    print("Authenticating with YouTube API...")
    token = client._get_access_token()
    print(f"Authenticated successfully (token ya29...{token[-6:]})")

    snippet = {
        "title": TITLE,
        "description": DESCRIPTION,
        "tags": TAGS,
        "categoryId": "10",  # Music category
    }
    status = {
        "privacyStatus": "private",  # Safe initial mode
        "selfDeclaredMadeForKids": False,
    }

    print("\n[Step 1/3] Initiating resumable upload session...")
    session_uri = client.insert_resumable_session(
        snippet=snippet,
        status=status,
        content_length=total_bytes,
    )
    print(f"Session established: {session_uri[:60]}...")

    print("\n[Step 2/3] Uploading video in chunks...")
    uploaded_bytes = 0
    video_resource = None

    start_time = time.time()
    while uploaded_bytes < total_bytes:
        chunk = video_bytes[uploaded_bytes : uploaded_bytes + CHUNK_SIZE]
        start_byte = uploaded_bytes
        resp = client.upload_video_chunk(
            session_uri=session_uri,
            chunk=chunk,
            start_byte=start_byte,
            total_bytes=total_bytes,
        )
        uploaded_bytes += len(chunk)
        pct = (uploaded_bytes / total_bytes) * 100.0
        elapsed = time.time() - start_time
        speed_kb = (uploaded_bytes / 1024.0) / (elapsed + 0.001)
        print(f"  Uploaded {uploaded_bytes:,} / {total_bytes:,} bytes ({pct:5.1f}%) - {speed_kb:.1f} KB/s")

        if resp is not None:
            video_resource = resp
            break

    if not video_resource or "id" not in video_resource:
        print("Upload did not return video resource!")
        sys.exit(1)

    video_id = video_resource["id"]
    print(f"\nUpload complete! YouTube Video ID: {video_id}")
    print(f"Watch URL: https://youtu.be/{video_id}")
    print(f"Studio Edit URL: https://studio.youtube.com/video/{video_id}/edit")

    # Step 3: Attach custom thumbnail
    if THUMBNAIL_PATH.is_file():
        print("\n[Step 3/3] Uploading custom thumbnail...")
        thumb_bytes = THUMBNAIL_PATH.read_bytes()
        try:
            client.set_thumbnail(video_id, thumb_bytes)
            print("Thumbnail uploaded and attached successfully!")
        except YouTubeClientError as e:
            print(f"Note on thumbnail: {e}")
    else:
        print(f"Thumbnail file {THUMBNAIL_PATH} not found; skipping thumbnail step.")

    # Check video processing status
    print("\nChecking video processing status from YouTube...")
    for _ in range(5):
        time.sleep(2)
        info = client.get_video(video_id)
        if info:
            status_obj = info.get("status", {})
            proc_obj = info.get("processingDetails", {})
            upload_status = status_obj.get("uploadStatus")
            proc_status = proc_obj.get("processingStatus")
            print(f"  uploadStatus: {upload_status}, processingStatus: {proc_status}")
            if upload_status == "processed" or proc_status == "succeeded":
                break

    print("\n" + "=" * 60)
    print("SUCCESS: Track successfully published to Rhodes & Rain Records!")
    print(f"Video ID: {video_id}")
    print(f"URL: https://youtu.be/{video_id}")
    print("Privacy: private (ready to review in YouTube Studio)")
    print("=" * 60)


if __name__ == "__main__":
    main()

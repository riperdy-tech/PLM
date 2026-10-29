"""Read-only audit of existing YouTube uploads; never publishes or changes metadata.

Usage:
  python scripts/diagnose_youtube_chapters.py VIDEO_ID --summary output_media/full_album/publication_summary.json
  python scripts/diagnose_youtube_chapters.py VIDEO_ID --api-json saved_youtube_video.json

The report checks the ACTUAL API description. Player chapter visibility is
always UNVERIFIED until an operator checks the video watch page.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.youtube_publishing.client.real import RealYouTubeClient
from services.youtube_publishing.diagnostics import inspect_uploaded_video


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only YouTube chapter diagnostics")
    parser.add_argument("video_id", help="Existing YouTube video id")
    parser.add_argument("--summary", type=Path, help="Original publication_summary.json")
    parser.add_argument("--api-json", type=Path, help="Saved videos.list JSON; run offline")
    args = parser.parse_args(argv)

    try:
        expected = None
        if args.summary is not None:
            summary: Any = json.loads(args.summary.read_text(encoding="utf-8"))
            if not isinstance(summary, dict) or not isinstance(summary.get("tracks"), list):
                raise ValueError("publication summary needs a tracks array")
            if summary.get("video_id") and summary["video_id"] != args.video_id:
                raise ValueError("publication summary video_id does not match the requested video")
            expected = summary["tracks"]
            if not all(isinstance(item, dict) for item in expected):
                raise ValueError("publication summary has invalid track entries")
        if args.api_json is not None:
            source: Any = json.loads(args.api_json.read_text(encoding="utf-8"))
            if isinstance(source, dict) and "items" in source:
                items = source["items"]
                resource = items[0] if isinstance(items, list) and len(items) == 1 else None
            else:
                resource = source
        else:
            client = RealYouTubeClient()
            if not client.is_configured:
                raise ValueError("YouTube OAuth read credentials are not configured")
            resource = client.get_video(args.video_id)
        if not isinstance(resource, dict) or resource.get("id") != args.video_id:
            raise ValueError("video details unavailable or returned video ID mismatch")

        result = inspect_uploaded_video(resource, now=datetime.now(UTC), expected_tracks=expected)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["description_chapters_valid"] else 2
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Chapter diagnostics failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

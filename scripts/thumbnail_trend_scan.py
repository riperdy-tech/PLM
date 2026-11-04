"""Read-only thumbnail trend scan: what packaging is winning right now in our niche.

Searches recent long-form focus-music videos through the official YouTube Data API (P1's
connector, API-key auth, ~100 quota units per query), ranks them by views per channel
subscriber (:mod:`market_intelligence.thumbnail_trends`) and saves, under
``data/thumbnail_trends/<date>/``:

* ``ranked.json``: every ranked video with its numbers;
* ``sheet.png``: a contact sheet of the top thumbnails, labelled with rank and ratio;
* ``thumbs/``: the downloaded thumbnails (reference only, never reused as artwork).

The style guide (``config/thumbnail_styles.json``) is then reviewed against the sheet.
Nothing is written to YouTube or to any database.

    python scripts/thumbnail_trend_scan.py [--days 90] [--top 30]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_intelligence.connectors.youtube import (
    YouTubeCredentials,
    YouTubeDataAPIConnector,
    parse_channel_stats,
    parse_search_hits,
    parse_video_stats,
)
from market_intelligence.thumbnail_trends import rank_outliers, thumbnail_urls
from plm_platform.config import Secret
from plm_platform.dotenv import load_env_file

QUERIES = (
    "deep focus music", "focus music for work", "music for coding", "deep work music",
    "study music concentration", "electronic focus music", "music for programming",
    "ambient electronic study music",
)
TILE = (320, 180)
COLUMNS = 5


def _api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY", "").strip() or load_env_file(
        REPO_ROOT / ".env").get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise SystemExit("YOUTUBE_API_KEY is not set (environment or .env)")
    return key


def _batches(ids: list[str], size: int = 50) -> list[list[str]]:
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def _sheet(entries: list, thumbs_dir: Path, out: Path) -> None:
    rows = (len(entries) + COLUMNS - 1) // COLUMNS
    label_h = 34
    sheet = Image.new("RGB", (COLUMNS * TILE[0], rows * (TILE[1] + label_h)), "#111111")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for n, entry in enumerate(entries):
        x, y = (n % COLUMNS) * TILE[0], (n // COLUMNS) * (TILE[1] + label_h)
        path = thumbs_dir / f"{entry.video_id}.jpg"
        if path.is_file():
            sheet.paste(Image.open(path).convert("RGB").resize(TILE), (x, y))
        draw.text((x + 4, y + TILE[1] + 2),
                  f"#{n + 1}  {entry.outlier_ratio:.1f}x subs  {entry.view_count:,} views",
                  fill="#f0f0f0", font=font)
        draw.text((x + 4, y + TILE[1] + 17), (entry.channel_title or "")[:44],
                  fill="#9a9a9a", font=font)
    sheet.save(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--per-query", type=int, default=25)
    args = parser.parse_args()

    now = datetime.now(UTC)
    out_dir = REPO_ROOT / "data" / "thumbnail_trends" / now.strftime("%Y-%m-%d")
    thumbs_dir = out_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=30) as http:
        api = YouTubeDataAPIConnector(
            client=http, credentials=YouTubeCredentials(api_key=Secret(_api_key())))
        video_ids: list[str] = []
        for query in QUERIES:
            hits = parse_search_hits(api.search_videos(
                query, max_results=args.per_query,
                published_after=now - timedelta(days=args.days)))
            video_ids += [h.video_id for h in hits if h.video_id not in video_ids]
        videos, urls = [], {}
        for batch in _batches(video_ids):
            fetch = api.fetch_videos(batch)
            videos += parse_video_stats(fetch)
            urls.update(thumbnail_urls(fetch))
        channel_ids = sorted({v.channel_id for v in videos if v.channel_id})
        channels = {}
        for batch in _batches(channel_ids):
            channels.update({c.channel_id: c for c in parse_channel_stats(api.fetch_channels(batch))})
        ranked = rank_outliers(videos, channels, urls, now=now, limit=args.top)
        for entry in ranked:
            if entry.thumbnail_url:
                resp = http.get(entry.thumbnail_url)
                if resp.status_code == 200:
                    Image.open(io.BytesIO(resp.content)).convert("RGB").save(
                        thumbs_dir / f"{entry.video_id}.jpg", quality=90)
        quota = api.quota_units_used

    (out_dir / "ranked.json").write_text(json.dumps({
        "scanned_at": now.isoformat(), "queries": QUERIES, "window_days": args.days,
        "candidates": len(video_ids), "quota_units": quota,
        "ranked": [{**{k: getattr(e, k) for k in e.__slots__},
                    "published_at": e.published_at.isoformat(),
                    "url": f"https://www.youtube.com/watch?v={e.video_id}"} for e in ranked],
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    _sheet(ranked, thumbs_dir, out_dir / "sheet.png")
    print(f"{len(ranked)} ranked of {len(video_ids)} candidates, {quota} quota units -> {out_dir}")


if __name__ == "__main__":
    main()

"""Offline read-only within-song repetition proxy report (P5 plan §24; no side effects).

Example:
  python scripts/probe_song_repetition.py --song stable_330s.wav --window-seconds 30
Reads WAV/FLAC/MP3 (whatever libsndfile reads). No provider, database, MinIO,
copyright or approval gate is consulted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from media_factory.audio.repetition_probe import probe_song_repetition


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline within-song repetition proxies")
    parser.add_argument("--song", required=True, metavar="LOCAL_FILE")
    parser.add_argument("--window-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        path = Path(args.song)
        if not path.is_file() or path.stat().st_size > 300_000_000:
            raise ValueError("missing or oversized local audio")
        report = probe_song_repetition(path.read_bytes(), window_seconds=args.window_seconds)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Offline repetition probe refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

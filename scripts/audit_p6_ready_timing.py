"""Read-only inventory of historical P6 READY package stream-duration QA evidence.

This does not re-probe any media bytes. It answers whether the saved report
contains the evidence needed to trust the current stream-level QA. It cannot
establish playback quality, rights or video-byte integrity.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import sqlalchemy as sa
from plm_platform.config import load_settings
from plm_platform.db import build_engine
from plm_platform.media_timing import classify_saved_media_timing

__all__ = ["audit_rows", "classify_report", "main"]

_LOCAL_DB_HOSTS = frozenset({"127.0.0.1", "localhost"})
_QUERY = sa.text(
    "SELECT status, video_duration_seconds, render_report_json "
    "FROM media.media_packages"
)


classify_report = classify_saved_media_timing


def audit_rows(rows: Iterable[tuple[str, float, str]]) -> dict[str, int]:
    """Return category counts only, not identifiers, URIs or other private facts."""
    counts: Counter[str] = Counter()
    for status, duration, raw_report in rows:
        counts[classify_report(status, duration, raw_report)] += 1
    return dict(sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-db", action="store_true",
                        help="explicitly allow read-only inspection of local development DB")
    args = parser.parse_args()
    if not args.local_db:
        parser.error("explicit --local-db required (no DB connection by default)")
    settings = load_settings(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    engine = build_engine(settings)
    try:
        if engine.url.host not in _LOCAL_DB_HOSTS:
            raise RuntimeError("refusing non-loopback P6 database")
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            counts = audit_rows(conn.execute(_QUERY).all())
        print(json.dumps(counts, sort_keys=True))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

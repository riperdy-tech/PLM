"""Read-only sample check of *actual* SHA256-verified P6 album audio.

Example:
  .\\.venv\\Scripts\\python.exe scripts\\diagnose_p6_album_texture_samples.py \\
    -p PACKAGE_A -p PACKAGE_B --samples-per-album 2

This is a bounded, operator-selected diagnostic, not an originality approval.
It writes no DB rows, MinIO objects or fingerprint evidence.
"""
from __future__ import annotations

import argparse
import json
import sys

import sqlalchemy as sa
from media_factory.artifacts import MinioArtifactStore
from media_factory.texture_sample_audit import (
    compare_cross_release_samples,
    load_sample_sources,
    measure_sources,
)
from plm_platform.config import load_settings
from plm_platform.db import build_engine
from sqlalchemy.orm import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only cross-album sample spectral similarity diagnostic"
    )
    parser.add_argument(
        "-p", "--package-id", action="append", required=True,
        help="Explicit ready/published P6 package (repeat 2..8 times)",
    )
    parser.add_argument(
        "--samples-per-album", type=int, default=2, choices=(1, 2, 3),
        help="Deterministic track samples per album (default 2)",
    )
    parser.add_argument(
        "--all-tracks", action="store_true",
        help="Explicit full-audio scan (max 8 albums, 30 tracks each, 200 tracks overall)",
    )
    args = parser.parse_args(argv)
    if args.all_tracks and args.samples_per_album != 2:
        parser.error("--samples-per-album is incompatible with --all-tracks")
    if not 2 <= len(args.package_id) <= 8 or len(set(args.package_id)) != len(
        args.package_id
    ):
        parser.error("provide 2..8 distinct package IDs")

    settings = load_settings()
    engine = build_engine(settings)
    try:
        with Session(engine) as session:
            # Explicit transaction-level read-only guarantee for all metadata.
            session.execute(sa.text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            ))
            try:
                sources, total_tracks = load_sample_sources(
                    session, args.package_id,
                    samples_per_album=args.samples_per_album,
                    full_album=args.all_tracks,
                )
            finally:
                session.rollback()
        store = MinioArtifactStore(settings)
        samples = measure_sources(store, sources)
        result = compare_cross_release_samples(samples, full_album=args.all_tracks)
        result["selected_packages"] = len(args.package_id)
        result["archive_tracks_across_selected_packages"] = total_tracks
        result["unsampled_tracks"] = total_tracks - len(samples)
        result["metadata_transaction"] = "repeatable_read_read_only"
        result["source_audio"] = "checksum_verified_minio_reads"
        result["stored_evidence"] = False
        result["human_listening_or_rights_clearance"] = False
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - a diagnostic fails closed, no partial pass
        print(json.dumps({
            "status": "unverifiable",
            "exception_type": type(exc).__name__,
            "stored_evidence": False,
        }), file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

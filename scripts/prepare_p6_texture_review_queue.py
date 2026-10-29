"""Read-only, bounded P6 cross-release review-queue preparation.

No audio samples, human labels or database/object records are written.
Scores in the output are observational, not rights clearance or deployment gates.
"""
from __future__ import annotations

import argparse
import json
import sys

import sqlalchemy as sa
from media_factory.artifacts import MinioArtifactStore
from media_factory.audio.texture_label_evaluation import EVALUATION, POLICY_VERSION
from media_factory.audio.texture_review_integrity import score_pair
from media_factory.audio.texture_review_selection import select_review_pairs
from media_factory.texture_sample_audit import load_sample_sources, measure_sources
from plm_platform.config import load_settings
from plm_platform.db import build_engine
from sqlalchemy.orm import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded read-only P6 human-review queue")
    parser.add_argument("-p", "--package-id", action="append", required=True)
    parser.add_argument("--samples-per-album", type=int, choices=(2, 3), default=2)
    parser.add_argument("--max-pairs", type=int, choices=range(2, 13), default=6)
    args = parser.parse_args(argv)
    if not 2 <= len(args.package_id) <= 8 or len(set(args.package_id)) != len(
        args.package_id
    ):
        parser.error("specify 2..8 distinct P6 package IDs")
    try:
        settings = load_settings()
        engine = build_engine(settings)
        try:
            with Session(engine) as session:
                session.execute(sa.text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                ))
                try:
                    sources, total = load_sample_sources(
                        session, args.package_id, samples_per_album=args.samples_per_album
                    )
                finally:
                    session.rollback()
        finally:
            engine.dispose()
        store = MinioArtifactStore(settings)
        measured = measure_sources(store, sources)
        selected = select_review_pairs(measured, max_pairs=args.max_pairs)
        by_source = {
            (source.package_id, source.song_id, source.sha256): source
            for source in sources
        }
        scores: list[dict[str, object]] = []
        cases: list[dict[str, object]] = []
        for candidate in selected:
            left, right = candidate.left, candidate.right
            left_source = by_source[(left.package_id, left.song_id, left.sha256)]
            right_source = by_source[(right.package_id, right.song_id, right.sha256)]
            # Keep only two checksum-verified byte buffers alive per pair.
            first = store.fetch_verified(left_source.audio_uri, left.sha256)
            second = store.fetch_verified(right_source.audio_uri, right.sha256)
            fixed, offset, rate = score_pair(first, second)
            del first, second
            scores.append({
                "left_sha256": left.sha256,
                "right_sha256": right.sha256,
                "fixed_score": fixed,
                "best_offset_score": offset,
                "best_rate_offset_score": rate,
            })
            cases.append({
                "left_package_id": left.package_id,
                "left_song_id": left.song_id,
                "left_sha256": left.sha256,
                "right_package_id": right.package_id,
                "right_song_id": right.song_id,
                "right_sha256": right.sha256,
                "relative_prescreen_band": candidate.relative_score_band,
                "prescreen_fixed_score": candidate.fixed_prescreen_score,
                "human_label": None,
            })
        result = {
            "scope": "explicit_sha256_verified_p6_source_sample_review_selection",
            "selection": "relative_spectral_strata_not_random_accuracy_sample",
            "source_metadata_transaction": "repeatable_read_read_only",
            "source_bytes": "checksum_verified_minio_reads",
            "selected_albums": len(args.package_id),
            "archived_tracks_in_selected_albums": total,
            "checksum_verified_sampled_tracks": len(measured),
            "selected_review_pairs": len(selected),
            "human_labels_supplied": 0,
            "stored_audio_or_db_changes": False,
            "release_gate_changed": False,
            "unlabelled_pair_score_report": {
                "policy_version": POLICY_VERSION,
                "evaluation": EVALUATION,
                "pairs": scores,
            },
            "review_queue": cases,
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - diagnostic refuses, never fabricates results
        print(json.dumps({
            "status": "unverifiable", "exception_type": type(exc).__name__,
            "stored_audio_or_db_changes": False,
        }), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

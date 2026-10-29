"""Read-only re-attestation of a saved P6 human listening review queue.

This verifies current P6 playlist metadata and SHA256-verified original bytes,
then independently recomputes each bounded rate/offset pair score. It never
authenticates reviewer identities, score authorship, or music rights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import sqlalchemy as sa
from media_factory.artifacts import MinioArtifactStore
from media_factory.audio.texture_review_integrity import (
    bind_review_queue_sources,
    score_pair,
    verify_review_queue_selection,
)
from media_factory.texture_sample_audit import load_sample_sources, measure_sources
from plm_platform.config import load_settings
from plm_platform.db import build_engine
from sqlalchemy.orm import Session

MAX_REPORT_BYTES = 300_000
MAX_SCORE_DIFFERENCE = 1e-8


def _unique_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_report_key")
        result[key] = value
    return result


def _refuse_nonfinite(_value: str) -> None:
    raise ValueError("nonfinite_report_score")


def _read_queue(path: Path) -> tuple[object, str]:
    with path.open("rb") as stream:
        content = stream.read(MAX_REPORT_BYTES + 1)
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("review_queue_report_too_large")
    return (
        json.loads(
            content.decode("utf-8-sig"),
            object_pairs_hook=_unique_keys,
            parse_constant=_refuse_nonfinite,
        ),
        hashlib.sha256(content).hexdigest(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute saved P6 listening queue scores using archived source bytes"
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("-p", "--package-id", action="append", required=True)
    parser.add_argument("--samples-per-album", type=int, choices=(2, 3), default=2)
    args = parser.parse_args(argv)
    if (
        not 2 <= len(args.package_id) <= 8
        or len(set(args.package_id)) != len(args.package_id)
    ):
        parser.error("require 2..8 distinct explicit archived package IDs")
    try:
        report, raw_sha256 = _read_queue(args.report)
        settings = load_settings()
        engine = build_engine(settings)
        try:
            with Session(engine) as session:
                session.execute(sa.text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                ))
                try:
                    sources, total = load_sample_sources(
                        session, args.package_id,
                        samples_per_album=args.samples_per_album,
                    )
                finally:
                    session.rollback()
        finally:
            engine.dispose()
        bound = bind_review_queue_sources(
            report, sources, selected_package_ids=args.package_id,
            samples_per_album=args.samples_per_album,
            archived_track_count=total,
        )
        lookup = {(source.package_id, source.song_id, source.sha256): source
                  for source in sources}
        store = MinioArtifactStore(settings)
        features = measure_sources(store, sources)
        verify_review_queue_selection(report, features, bound)
        for left, right, claimed in bound:
            # Re-fetch from the exact selected metadata source, not report-supplied URIs.
            first = lookup[(left.package_id, left.song_id, left.sha256)]
            second = lookup[(right.package_id, right.song_id, right.sha256)]
            lhs = store.fetch_verified(first.audio_uri, first.sha256)
            rhs = store.fetch_verified(second.audio_uri, second.sha256)
            measured = score_pair(lhs, rhs)
            del lhs, rhs
            if any(
                not math.isclose(expected, actual, rel_tol=0, abs_tol=MAX_SCORE_DIFFERENCE)
                for expected, actual in zip(claimed, measured, strict=True)
            ):
                raise ValueError("saved_queue_score_does_not_reproduce")
        print(json.dumps({
            "status": "source_bound_scores_recomputed",
            "score_report_sha256": raw_sha256,
            "validated_source_pair_count": len(bound),
            "original_sha256_verifications": len(sources) + 2 * len(bound),
            "selection_replayed_from_verified_audio": True,
            "metadata": "read_only_repeatable_read",
            "stored_data_changed": False,
            "human_review_or_rights_verified": False,
            "production_gate_ready": False,
        }, sort_keys=True))
        return 0
    except (OSError, UnicodeError, TypeError, ValueError, KeyError) as exc:
        print(json.dumps({
            "status": "review_queue_unverifiable",
            "reason_type": type(exc).__name__,
            "stored_data_changed": False,
        }, sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - keep unexpected infra error redacted
        print(json.dumps({
            "status": "review_queue_unverifiable",
            "reason_type": type(exc).__name__,
            "stored_data_changed": False,
        }, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

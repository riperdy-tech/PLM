"""Read-only evaluation of supplied P6 rate/offset pair scores and human labels.

Usage: python scripts/evaluate_p6_texture_labels.py --report pair_scores.json
       [--reviews human_reviews.csv]
Does not extract audio, verify JSON provenance, calibrate a release threshold
or treat missing/disputed labels as negative examples.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

from media_factory.audio.texture_label_evaluation import (
    MAX_REVIEWS,
    REVIEW_COLUMNS,
    evaluate_labeled_pair_scores,
)


def _read_bounded(path: Path, maximum: int) -> str:
    if not path.is_file():
        raise ValueError("missing_input_file")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("input_exceeds_limit")
    return raw.decode("utf-8-sig")


def _object_with_unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite_json(_value: str) -> None:
    raise ValueError("nonfinite_json_number")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, labelled P6 rate-offset score audit")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--reviews", type=Path, action="append",
        help="A separate human-review CSV (repeat per reviewer; optional)",
    )
    args = parser.parse_args(argv)
    try:
        report = json.loads(
            _read_bounded(args.report, 300_000),
            object_pairs_hook=_object_with_unique_keys,
            parse_constant=_nonfinite_json,
        )
        if isinstance(report, dict) and "unlabelled_pair_score_report" in report:
            report = report["unlabelled_pair_score_report"]
        rows: list[dict[str, str | None]] = []
        for review_path in args.reviews or []:
            reader = csv.DictReader(
                io.StringIO(_read_bounded(review_path, 250_000)), strict=True,
            )
            if (
                reader.fieldnames is None
                or tuple(reader.fieldnames) != REVIEW_COLUMNS
            ):
                raise ValueError("invalid_review_csv_header")
            rows.extend(reader)
            if len(rows) > MAX_REVIEWS:
                raise ValueError("review_row_limit_exceeded")
        result = evaluate_labeled_pair_scores(report, rows)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, TypeError, ValueError, csv.Error) as exc:
        # Never leak source paths, review labels or reviewer pseudonyms to error logs.
        print(f"Texture label evaluation refused: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

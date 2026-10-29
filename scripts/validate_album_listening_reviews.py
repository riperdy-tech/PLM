"""Read-only validation of operator-supplied human listening-review CSV.

Example:
 python scripts/validate_album_listening_reviews.py --report contrast.json --reviews reviews.csv
Never infers human labels, rights, calibrated thresholds or release clearance.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from media_factory.audio.listening_review import REVIEW_COLUMNS, validate_listening_reviews


def _read_limited(path: Path, maximum: int) -> str:
    if not path.is_file():
        raise ValueError("missing_local_input")
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("input_too_large")
    return payload.decode("utf-8-sig")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate_json_key")
        result[name] = value
    return result


def _reject_nonfinite_json(_value: str) -> None:
    raise ValueError("nonfinite_json_number")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate offline listening review provenance and labels")
    parser.add_argument("--report", type=Path, required=True, help="Saved local diagnostic JSON")
    parser.add_argument("--reviews", type=Path, required=True, help="Operator-provided CSV, never generated")
    args = parser.parse_args(argv)
    try:
        report = json.loads(
            _read_limited(args.report, 300_000),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
        reader = csv.DictReader(
            io.StringIO(_read_limited(args.reviews, 450_000)), strict=True,
        )
        if reader.fieldnames is None or set(reader.fieldnames) != set(REVIEW_COLUMNS):
            raise ValueError("invalid_review_csv_header")
        if len(reader.fieldnames) != len(REVIEW_COLUMNS):
            raise ValueError("duplicate_review_csv_columns")
        rows = list(reader)
        if len(rows) > 1000:
            raise ValueError("review_row_limit_exceeded")
        result = validate_listening_reviews(report, rows)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, TypeError, ValueError, csv.Error) as exc:
        # Never print submitted row values, notes or reviewer identities.
        print(f"Listening review validation refused: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

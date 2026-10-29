"""Read-only sample confusion matrices from separately supplied human pair labels.

A different SHA256 proves different file bytes, not different compositions.
The diagnostic never infers human labels, calibrates thresholds, or clears rights.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

POLICY_VERSION = "p6-rate-offset-pairs-v1"
EVALUATION = "diagnostic_only_no_clearance"
REVIEW_COLUMNS = (
    "left_sha256", "right_sha256", "reviewer_id", "relationship", "evidence_ref",
)
SCORE_KEYS = ("fixed_score", "best_offset_score", "best_rate_offset_score")
RELATIONSHIPS = frozenset(("same_recording", "different_music", "uncertain"))
SHA = re.compile(r"[0-9a-f]{64}\Z")
REFERENCE = re.compile(r"[A-Za-z0-9_.:-]{1,96}\Z")
MAX_PAIRS = 200
MAX_REVIEWS = 1000
REFERENCE_THRESHOLD = 0.998

PairKey = tuple[str, str]


def _pair(first: object, second: object) -> PairKey:
    if (
        not isinstance(first, str) or not SHA.fullmatch(first)
        or not isinstance(second, str) or not SHA.fullmatch(second)
        or first == second
    ):
        raise ValueError("invalid_distinct_sha256_pair")
    return (min(first, second), max(first, second))


def _scores(report: object) -> dict[PairKey, tuple[float, float, float]]:
    if not isinstance(report, dict):
        raise TypeError("invalid_pair_score_report")
    if (
        report.get("policy_version") != POLICY_VERSION
        or report.get("evaluation") != EVALUATION
    ):
        raise ValueError("unrecognized_or_unapproved_diagnostic")
    entries = report.get("pairs")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PAIRS:
        raise ValueError("invalid_pair_count")
    verified: dict[PairKey, tuple[float, float, float]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "left_sha256", "right_sha256", *SCORE_KEYS,
        }:
            raise ValueError("invalid_pair_score_fields")
        key = _pair(entry["left_sha256"], entry["right_sha256"])
        if key in verified:
            raise ValueError("duplicate_source_pair")
        scores = tuple(entry[name] for name in SCORE_KEYS)
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1
            for value in scores
        ):
            raise ValueError("invalid_pair_score")
        if scores[1] + 1e-10 < scores[0] or scores[2] + 1e-10 < scores[1]:
            raise ValueError("inconsistent_nested_search_scores")
        verified[key] = (float(scores[0]), float(scores[1]), float(scores[2]))
    return verified


def evaluate_labeled_pair_scores(
    report: object, rows: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Report descriptive confusion counts only when >=2 reviewers agree per pair."""
    scores = _scores(report)
    if len(rows) > MAX_REVIEWS:
        raise ValueError("review_row_limit_exceeded")
    by_pair: dict[PairKey, dict[str, str]] = defaultdict(dict)
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != set(REVIEW_COLUMNS):
            raise ValueError("invalid_review_columns")
        key = _pair(row["left_sha256"], row["right_sha256"])
        if key not in scores:
            raise ValueError("review_pair_not_scored")
        reviewer = row["reviewer_id"]
        if not isinstance(reviewer, str) or not REFERENCE.fullmatch(reviewer):
            raise ValueError("invalid_reviewer_pseudonym")
        if reviewer in by_pair[key]:
            raise ValueError("duplicate_reviewer_per_source_pair")
        label = row["relationship"]
        if not isinstance(label, str) or label not in RELATIONSHIPS:
            raise ValueError("invalid_relationship_label")
        evidence = row["evidence_ref"]
        if (
            not isinstance(evidence, str)
            or (label != "uncertain" and not REFERENCE.fullmatch(evidence))
            or (label == "uncertain" and evidence and not REFERENCE.fullmatch(evidence))
        ):
            raise ValueError("missing_or_invalid_review_evidence_ref")
        by_pair[key][reviewer] = label

    agreed: dict[PairKey, str] = {}
    for key, reviewers in by_pair.items():
        labels = set(reviewers.values())
        if len(reviewers) >= 2 and len(labels) == 1 and "uncertain" not in labels:
            agreed[key] = next(iter(labels))
    positive = sum(label == "same_recording" for label in agreed.values())
    negative = sum(label == "different_music" for label in agreed.values())
    methods: dict[str, dict[str, int | float | None]] = {}
    for idx, name in enumerate(SCORE_KEYS):
        tp = fn = tn = fp = 0
        for key, label in agreed.items():
            predicted = scores[key][idx] >= REFERENCE_THRESHOLD
            if label == "same_recording":
                tp += int(predicted)
                fn += int(not predicted)
            else:
                fp += int(predicted)
                tn += int(not predicted)
        methods[name] = {
            "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "sample_true_positive_fraction": tp / positive if positive else None,
            "sample_false_positive_fraction": fp / negative if negative else None,
        }

    return {
        "policy_version": POLICY_VERSION,
        "status": "sample_diagnostic_only_no_clearance",
        "reference_threshold_not_calibrated": REFERENCE_THRESHOLD,
        "source_pair_count": len(scores),
        "reviewed_pair_count": len(by_pair),
        "adjudicated_same_recording_pairs": positive,
        "adjudicated_different_music_pairs": negative,
        "unadjudicated_pairs": len(scores) - len(agreed),
        "review_rows_count": len(rows),
        "distinct_reviewer_pseudonyms_count": len({
            reviewer for reviewers in by_pair.values() for reviewer in reviewers
        }),
        "sample_confusion": methods,
        "claims_not_verified": [
            "score_report_authenticity", "human_review_independence",
            "human_label_correctness", "population_accuracy",
            "rights_and_originality_clearance",
        ],
        "operational_threshold_ready": False,
    }

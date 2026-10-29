"""Validate human labels against a saved offline album-contrast report.

Read-only: supplied hashes and reviewer records cannot establish provenance,
rights, calibrated musical similarity or release approval.
"""
from __future__ import annotations

import itertools
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .contrast_probe import POLICY_VERSION

LABELS = frozenset({"similar", "different", "uncertain"})
AXES = ("rhythm", "melody", "instrumentation", "structure")
METRICS = ("rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy")
REVIEW_COLUMNS = (
    "left_id", "right_id", "left_sha256", "right_sha256", "policy_version",
    "reviewer_id", "rhythm_label", "melody_label", "instrumentation_label",
    "structure_label", "same_recording_source", "notes",
)
HASH = re.compile(r"[0-9a-f]{64}\Z")
REVIEWER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _valid_report(
    report: object,
) -> tuple[dict[str, str], dict[tuple[str, str], dict[str, bool]], set[str]]:
    if not isinstance(report, dict):
        raise TypeError("expected_object_report")
    if report.get("policy_version") != POLICY_VERSION:
        raise ValueError("report_policy_version_mismatch")
    if report.get("evaluation") != "diagnostic_only_no_threshold_no_clearance":
        raise ValueError("report_not_diagnostic")
    coverage, pairs = report.get("coverage"), report.get("pairs")
    checks = report.get("provided_checksum_verified", [])
    if not isinstance(coverage, dict) or not 2 <= len(coverage) <= 20:
        raise ValueError("report_invalid_coverage")
    if not isinstance(pairs, list) or not isinstance(checks, list):
        raise TypeError("report_invalid_pairs_or_checksums")
    digests: dict[str, str] = {}
    unavailable: dict[str, set[str]] = {}
    unknown_by_sha: dict[str, dict[str, str]] = {}
    for alias, source in coverage.items():
        if (
            not isinstance(alias, str) or not alias.strip() or len(alias) > 128
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in alias)
            or not isinstance(source, dict)
        ):
            raise ValueError("report_invalid_coverage")
        sha = source.get("sha256")
        if not isinstance(sha, str) or not HASH.fullmatch(sha):
            raise ValueError("report_invalid_source_checksum")
        unknown = source.get("unknown")
        if (
            not isinstance(unknown, dict)
            or any(
                not isinstance(metric, str) or metric not in METRICS
                or not isinstance(reason, str) or not reason.strip()
                or len(reason) > 128
                for metric, reason in unknown.items()
            )
        ):
            raise ValueError("report_invalid_unknown")
        # An original byte digest fixes the result of deterministic analysis.
        # Aliases of the same bytes cannot disagree on unavailable metrics.
        previous_unknown = unknown_by_sha.get(sha)
        if previous_unknown is not None and previous_unknown != unknown:
            raise ValueError("report_same_source_unknown_conflict")
        unknown_by_sha[sha] = unknown
        digests[alias] = sha
        unavailable[alias] = set(unknown)
    checked: set[str] = set()
    for value in checks:
        if not isinstance(value, str) or value not in digests or value in checked:
            raise ValueError("report_invalid_checksum_attestation")
        checked.add(value)
    expected = set(itertools.combinations(sorted(digests), 2))
    if len(pairs) != len(expected):
        raise ValueError("report_incomplete_pairs")
    available_pairs: dict[tuple[str, str], dict[str, bool]] = {}
    metrics_by_source_pair: dict[tuple[str, str], dict[str, object]] = {}
    for pair in pairs:
        if not isinstance(pair, dict):
            raise TypeError("report_invalid_pair")
        left, right = pair.get("left"), pair.get("right")
        if not isinstance(left, str) or not isinstance(right, str):
            raise TypeError("report_invalid_pair")
        key = tuple(sorted((left, right)))
        if len(set(key)) != 2 or key not in expected or key in available_pairs:
            raise ValueError("report_duplicate_or_unknown_pair")
        metrics = pair.get("metrics")
        if not isinstance(metrics, dict) or set(metrics) != set(METRICS):
            raise ValueError("report_invalid_metric")
        available: dict[str, bool] = {}
        for name in METRICS:
            value = metrics[name]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0.0 <= value <= 1.0
            ):
                raise ValueError("report_invalid_metric")
            expected_available = (
                name not in unavailable[left] and name not in unavailable[right]
            )
            if (value is not None) != expected_available:
                raise ValueError("report_metric_coverage_conflict")
            available[name] = value is not None
        left_digest, right_digest = digests[left], digests[right]
        source_pair = (min(left_digest, right_digest), max(left_digest, right_digest))
        if left_digest == right_digest and any(
            value is not None and value != 1.0 for value in metrics.values()
        ):
            raise ValueError("report_identical_source_similarity_conflict")
        # Distinct alias pairs for the same two byte sources must reproduce
        # the same scores; otherwise the report contradicts its own inputs.
        prior_metrics = metrics_by_source_pair.get(source_pair)
        if prior_metrics is not None and prior_metrics != metrics:
            raise ValueError("report_source_alias_metric_conflict")
        metrics_by_source_pair[source_pair] = metrics
        available_pairs[key] = available
    if set(available_pairs) != expected:
        raise ValueError("report_incomplete_pairs")
    return digests, available_pairs, checked


def _reviewer_overlap_diagnostic(
    digests: Mapping[str, str],
    reviews: Mapping[tuple[str, str], Mapping[str, Mapping[str, str]]],
) -> dict[str, object]:
    """Describe shared reviewer assignments without claiming independence.

    One reviewer and one unordered, different-byte source comparison count
    once, even if that reviewer assessed multiple aliases of the same bytes.
    Conflicting alias labels are retained as ambiguous, never picked by order.
    """
    source_reviews: dict[
        tuple[str, str], dict[str, dict[str, set[str]]]
    ] = {}
    for (left, right), reviewers in reviews.items():
        source_a, source_b = digests[left], digests[right]
        if source_a == source_b:
            continue
        source_pair = (min(source_a, source_b), max(source_a, source_b))
        source_raters = source_reviews.setdefault(source_pair, {})
        for reviewer, labels in reviewers.items():
            ratings = source_raters.setdefault(reviewer, {axis: set() for axis in AXES})
            for axis in AXES:
                ratings[axis].add(labels[axis])

    shared_counts: Counter[tuple[str, str]] = Counter()
    ambiguous_alias_groups = dict.fromkeys(AXES, 0)
    axes = {
        axis: {"agreement": 0, "disagreement": 0, "ambiguous_or_uncertain": 0}
        for axis in AXES
    }
    for raters in source_reviews.values():
        for ratings in raters.values():
            for axis in AXES:
                if len(ratings[axis]) > 1:
                    ambiguous_alias_groups[axis] += 1
        for first, second in itertools.combinations(sorted(raters), 2):
            shared_counts[(first, second)] += 1
            for axis in AXES:
                first_labels, second_labels = raters[first][axis], raters[second][axis]
                if (
                    len(first_labels) != 1 or len(second_labels) != 1
                    or "uncertain" in first_labels or "uncertain" in second_labels
                ):
                    axes[axis]["ambiguous_or_uncertain"] += 1
                elif first_labels == second_labels:
                    axes[axis]["agreement"] += 1
                else:
                    axes[axis]["disagreement"] += 1

    axis_quality: dict[str, dict[str, int | float | None]] = {}
    for axis, counts in axes.items():
        decisive = counts["agreement"] + counts["disagreement"]
        axis_quality[axis] = {
            **counts,
            "decisive_co_rating_count": decisive,
            "descriptive_agreement_fraction": (
                round(counts["agreement"] / decisive, 6) if decisive else None
            ),
        }
    return {
        "different_sha256_comparisons_with_two_plus_reviewers_count": sum(
            len(raters) >= 2 for raters in source_reviews.values()
        ),
        "reviewer_pairs_with_shared_source_comparisons_count": len(shared_counts),
        "reviewer_pairs_with_two_plus_shared_source_comparisons_count": sum(
            count >= 2 for count in shared_counts.values()
        ),
        "max_shared_source_comparisons_per_reviewer_pair": max(
            shared_counts.values(), default=0,
        ),
        "conflicting_alias_label_groups_by_axis": ambiguous_alias_groups,
        "axis_co_ratings": axis_quality,
    }


def validate_listening_reviews(
    report: object,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Cross-check separate human labels; do not infer reviewer judgment."""
    digests, scores, checked = _valid_report(report)
    digest_alias_counts = Counter(digests.values())
    same_digest_pairs = {
        pair for pair in scores if digests[pair[0]] == digests[pair[1]]
    }
    digest_pair_counts = Counter(
        tuple(sorted((digests[left], digests[right])))
        for left, right in scores if (left, right) not in same_digest_pairs
    )
    unique_digest_pair_aliases = {
        (left, right) for left, right in scores
        if (left, right) not in same_digest_pairs
        and digest_pair_counts[tuple(sorted((digests[left], digests[right])))] == 1
    }
    # Different SHA256 means different source bytes, not different songs.
    # Multiple pairs may share the same source, so pair counts are not
    # statistically independent sample counts.
    distinct_digest_incidence: Counter[str] = Counter()
    for left, right in scores:
        if (left, right) not in same_digest_pairs:
            distinct_digest_incidence[digests[left]] += 1
            distinct_digest_incidence[digests[right]] += 1
    if len(rows) > 1000:
        raise ValueError("review_row_limit_exceeded")
    reviews: dict[tuple[str, str], dict[str, dict[str, str]]] = defaultdict(dict)
    # Keep only evidence-reference presence, never submitted references or notes.
    source_reference_presence: list[tuple[tuple[str, str], str, bool]] = []
    for position, row in enumerate(rows, start=2):
        if not isinstance(row, Mapping) or set(row) != set(REVIEW_COLUMNS):
            raise ValueError(f"review_columns_invalid_at_line_{position}")
        left, right = _string(row["left_id"]), _string(row["right_id"])
        pair = tuple(sorted((left, right)))
        if len(set(pair)) != 2 or pair not in scores:
            raise ValueError(f"review_unknown_pair_at_line_{position}")
        if row["left_sha256"] != digests[left] or row["right_sha256"] != digests[right]:
            raise ValueError(f"review_checksum_mismatch_at_line_{position}")
        if row["policy_version"] != POLICY_VERSION:
            raise ValueError(f"review_policy_mismatch_at_line_{position}")
        reviewer = _string(row["reviewer_id"])
        if not REVIEWER.fullmatch(reviewer):
            raise ValueError(f"reviewer_pseudonym_invalid_at_line_{position}")
        if reviewer in reviews[pair]:
            raise ValueError(f"duplicate_reviewer_for_pair_at_line_{position}")
        for axis in AXES:
            label = row[f"{axis}_label"]
            if not isinstance(label, str) or label not in LABELS:
                raise ValueError(f"review_label_invalid_at_line_{position}")
        if (
            not isinstance(row["same_recording_source"], str)
            or len(row["same_recording_source"]) > 128
        ):
            raise ValueError(f"review_source_invalid_at_line_{position}")
        if not isinstance(row["notes"], str) or len(row["notes"]) > 500:
            raise ValueError(f"review_notes_invalid_at_line_{position}")
        reviews[pair][reviewer] = {axis: str(row[f"{axis}_label"]) for axis in AXES}
        left_digest, right_digest = digests[left], digests[right]
        source_reference_presence.append((
            (min(left_digest, right_digest), max(left_digest, right_digest)),
            reviewer,
            bool(row["same_recording_source"].strip()),
        ))

    # Only aggregate pseudonyms. Repeated assignments do not prove independent
    # listeners, and an alias change cannot make the same byte comparison new.
    reviewer_pair_counts = Counter(
        reviewer for pair_reviews in reviews.values() for reviewer in pair_reviews
    )
    reviewer_source_comparisons = Counter(
        (reviewer, tuple(sorted((digests[left], digests[right]))))
        for (left, right), pair_reviews in reviews.items()
        for reviewer in pair_reviews
    )
    source_reference_by_comparison: dict[tuple[str, str], set[bool]] = defaultdict(set)
    source_reference_by_reviewer: dict[tuple[tuple[str, str], str], set[bool]] = (
        defaultdict(set)
    )
    for source_pair, reviewer, provided in source_reference_presence:
        source_reference_by_comparison[source_pair].add(provided)
        source_reference_by_reviewer[(source_pair, reviewer)].add(provided)
    # Presence is an operator claim only; no raw evidence strings are retained.
    source_reference_quality = {
        "nonempty_reference_review_row_count": sum(
            provided for _, _, provided in source_reference_presence
        ),
        "reviewed_sha256_comparison_count": len(source_reference_by_comparison),
        "sha256_comparisons_with_nonempty_reference_count": sum(
            True in presence for presence in source_reference_by_comparison.values()
        ),
        "sha256_comparisons_with_mixed_reference_presence_count": sum(
            len(presence) > 1 for presence in source_reference_by_comparison.values()
        ),
        "reviewer_sha256_comparison_groups_with_mixed_reference_presence_count": sum(
            len(presence) > 1 for presence in source_reference_by_reviewer.values()
        ),
        "identical_sha256_review_rows_with_nonempty_reference_count": sum(
            left == right and provided
            for (left, right), _, provided in source_reference_presence
        ),
    }

    # Aggregate distinct audio pairs, not raw CSV rows: a heavily reviewed
    # track cannot inflate the denominator simply by having more reviewers.
    summary = {
        axis: {
            "insufficient_reviewers": 0,
            "unresolved": 0,
            "disagreement": 0,
            "unanimous_similar": 0,
            "unanimous_different": 0,
        }
        for axis in AXES
    }
    distinct_digest_axis = {
        axis: {"decisive": 0, "unanimous": 0} for axis in AXES
    }
    unique_digest_axis = {
        axis: {"decisive": 0, "unanimous": 0} for axis in AXES
    }
    metric_coverage = {metric: sum(
        availability[metric] for availability in scores.values()
    ) for metric in METRICS}
    result_pairs: list[dict[str, object]] = []
    for pair in sorted(scores):
        observed = list(reviews[pair].values())
        axis_status: dict[str, str] = {}
        for axis in AXES:
            labels = [item[axis] for item in observed]
            if len(labels) < 2:
                axis_status[axis] = "insufficient_reviewers"
            elif "uncertain" in labels:
                axis_status[axis] = "unresolved"
            elif len(set(labels)) > 1:
                axis_status[axis] = "disagreement"
            else:
                axis_status[axis] = "unanimous"
            category = (
                f"unanimous_{labels[0]}"
                if axis_status[axis] == "unanimous"
                else axis_status[axis]
            )
            summary[axis][category] += 1
            if pair not in same_digest_pairs and axis_status[axis] in {
                "unanimous", "disagreement",
            }:
                distinct_digest_axis[axis]["decisive"] += 1
                if axis_status[axis] == "unanimous":
                    distinct_digest_axis[axis]["unanimous"] += 1
                if pair in unique_digest_pair_aliases:
                    unique_digest_axis[axis]["decisive"] += 1
                    if axis_status[axis] == "unanimous":
                        unique_digest_axis[axis]["unanimous"] += 1
        result_pairs.append({
            "left": pair[0],
            "right": pair[1],
            "exact_same_sha256": pair in same_digest_pairs,
            "unique_different_sha256_comparison": pair in unique_digest_pair_aliases,
            "review_count": len(observed),
            "axis_status": axis_status,
            "metric_available": scores[pair],
            "caller_digest_match_declared": all(alias in checked for alias in pair),
            "review_complete": all(value == "unanimous" for value in axis_status.values()),
        })
    axis_quality: dict[str, dict[str, int | float | None]] = {}
    for axis, counts in summary.items():
        decisive = (
            counts["unanimous_similar"]
            + counts["unanimous_different"]
            + counts["disagreement"]
        )
        unanimous = counts["unanimous_similar"] + counts["unanimous_different"]
        axis_quality[axis] = {
            **counts,
            "decisive_pair_count": decisive,
            "decisive_agreement_fraction": (
                round(unanimous / decisive, 6) if decisive else None
            ),
            "different_sha256_decisive_pair_count": distinct_digest_axis[axis]["decisive"],
            "different_sha256_agreement_fraction": (
                round(
                    distinct_digest_axis[axis]["unanimous"]
                    / distinct_digest_axis[axis]["decisive"], 6,
                )
                if distinct_digest_axis[axis]["decisive"] else None
            ),
            "unique_sha256_comparison_decisive_pair_count": unique_digest_axis[axis]["decisive"],
            "unique_sha256_comparison_agreement_fraction": (
                round(
                    unique_digest_axis[axis]["unanimous"]
                    / unique_digest_axis[axis]["decisive"], 6,
                )
                if unique_digest_axis[axis]["decisive"] else None
            ),
        }

    return {
        "policy_version": POLICY_VERSION,
        "evaluation": "review_integrity_only_no_threshold_no_clearance",
        "data_quality": {
            "unique_source_sha256_count": len(digest_alias_counts),
            "sha256_reused_by_multiple_aliases_count": sum(
                count > 1 for count in digest_alias_counts.values()
            ),
            "exact_same_sha256_pair_count": len(same_digest_pairs),
            "different_sha256_pair_count": len(scores) - len(same_digest_pairs),
            "unique_different_sha256_comparison_count": len(unique_digest_pair_aliases),
            "repeated_different_sha256_comparison_group_count": sum(
                multiplicity > 1 for multiplicity in digest_pair_counts.values()
            ),
            "max_different_sha256_pairs_sharing_one_source": max(
                distinct_digest_incidence.values(), default=0,
            ),
            "unique_reviewer_pseudonym_count": len(reviewer_pair_counts),
            "max_reviewed_pairs_per_reviewer": max(
                reviewer_pair_counts.values(), default=0,
            ),
            "reviewers_covering_multiple_pairs_count": sum(
                count > 1 for count in reviewer_pair_counts.values()
            ),
            "repeated_sha256_comparison_reviewer_count": sum(
                count > 1 for count in reviewer_source_comparisons.values()
            ),
            "extra_reviews_same_sha256_comparison_by_reviewer": sum(
                count - 1 for count in reviewer_source_comparisons.values()
            ),
            "no_review_pair_count": sum(not reviews[p] for p in scores),
            "one_review_pair_count": sum(len(reviews[p]) == 1 for p in scores),
            "two_plus_review_pair_count": sum(len(reviews[p]) >= 2 for p in scores),
            "caller_digest_pair_count": sum(
                all(alias in checked for alias in pair) for pair in scores
            ),
            "metric_available_pair_count": metric_coverage,
            "axis_quality": axis_quality,
            "reviewer_overlap": _reviewer_overlap_diagnostic(digests, reviews),
            "operator_source_reference": source_reference_quality,
        },
        "pair_count": len(result_pairs),
        "reviewed_pair_count": sum(bool(reviews[p]) for p in scores),
        "complete_consensus_pair_count": sum(
            bool(item["review_complete"]) for item in result_pairs
        ),
        "pairs": result_pairs,
        "rights_verified": False,
        "provenance_independently_verified": False,
        "calibrated": False,
        "limitations": [
            "reviewer labels are operator-supplied and not independently authenticated",
            "matching caller SHA256 does not prove original provenance or rights",
            "unanimity cannot establish correct assessments or calibrated thresholds",
            "same SHA256 means identical bytes; differing hashes do not prove distinct songs",
            "multiple audio pairs may reuse one source and are not independent samples",
            "no review outcome can automatically approve or block a release",
        ],
    }

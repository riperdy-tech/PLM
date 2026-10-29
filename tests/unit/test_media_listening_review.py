"""Synthetic-only tests for offline listening review integrity (no real labels)."""
from __future__ import annotations

import copy
import csv
import io
import json

import pytest
from media_factory.audio.contrast_probe import POLICY_VERSION
from media_factory.audio.listening_review import REVIEW_COLUMNS, validate_listening_reviews

from scripts import validate_album_listening_reviews


@pytest.fixture
def report() -> dict:
    return {
        "policy_version": POLICY_VERSION,
        "evaluation": "diagnostic_only_no_threshold_no_clearance",
        "coverage": {
            "A": {"sha256": "a" * 64, "unknown": {}},
            "B": {"sha256": "b" * 64, "unknown": {"energy_arc_proxy": "silent"}},
        },
        "pairs": [{
            "left": "A",
            "right": "B",
            "metrics": {
                "rhythm_onset_proxy": 0.92,
                "pitch_class_motion_proxy": 0.88,
                "energy_arc_proxy": None,
            },
        }],
        "provided_checksum_verified": ["A"],
    }


def _row(reviewer: str, labels: tuple[str, str, str, str] = (
    "similar", "similar", "different", "different",
)) -> dict[str, str]:
    return {
        "left_id": "A", "right_id": "B",
        "left_sha256": "a" * 64, "right_sha256": "b" * 64,
        "policy_version": POLICY_VERSION,
        "reviewer_id": reviewer,
        "rhythm_label": labels[0], "melody_label": labels[1],
        "instrumentation_label": labels[2], "structure_label": labels[3],
        "same_recording_source": "", "notes": "fictional private observations",
    }


def test_no_reviews_is_not_concordance(report: dict) -> None:
    result = validate_listening_reviews(report, [])
    assert result["reviewed_pair_count"] == 0
    assert result["complete_consensus_pair_count"] == 0
    assert result["pairs"][0]["axis_status"]["rhythm"] == "insufficient_reviewers"
    assert result["pairs"][0]["metric_available"]["energy_arc_proxy"] is False
    assert not result["pairs"][0]["caller_digest_match_declared"]
    assert result["rights_verified"] is False
    assert result["calibrated"] is False


def test_one_review_does_not_count_as_agreement(report: dict) -> None:
    result = validate_listening_reviews(report, [_row("r1")])
    assert result["reviewed_pair_count"] == 1
    assert result["complete_consensus_pair_count"] == 0


def test_independent_matching_labels_count_only_as_consensus(report: dict) -> None:
    report["provided_checksum_verified"] = ["A", "B"]
    result = validate_listening_reviews(report, [_row("r1"), _row("r2")])
    pair = result["pairs"][0]
    assert pair["review_complete"] is True
    assert pair["caller_digest_match_declared"] is True
    assert result["complete_consensus_pair_count"] == 1
    assert result["provenance_independently_verified"] is False
    assert result["rights_verified"] is False
    assert result["calibrated"] is False
    assert "r1" not in json.dumps(result)
    assert "fictional private observations" not in json.dumps(result)


def test_quality_empty_and_one_review_have_null_agreement(report: dict) -> None:
    empty = validate_listening_reviews(report, [])["data_quality"]
    assert empty["no_review_pair_count"] == 1
    assert empty["one_review_pair_count"] == 0
    assert empty["two_plus_review_pair_count"] == 0
    assert empty["caller_digest_pair_count"] == 0
    assert empty["metric_available_pair_count"] == {
        "rhythm_onset_proxy": 1,
        "pitch_class_motion_proxy": 1,
        "energy_arc_proxy": 0,
    }
    assert all(
        axis["insufficient_reviewers"] == 1
        and axis["decisive_pair_count"] == 0
        and axis["decisive_agreement_fraction"] is None
        for axis in empty["axis_quality"].values()
    )
    one = validate_listening_reviews(report, [_row("anon1")])["data_quality"]
    assert one["no_review_pair_count"] == 0
    assert one["one_review_pair_count"] == 1
    assert one["two_plus_review_pair_count"] == 0
    assert one["axis_quality"]["rhythm"]["decisive_agreement_fraction"] is None


def test_quality_separates_unanimity_disagreement_and_uncertain(report: dict) -> None:
    result = validate_listening_reviews(report, [
        _row("r1"), _row("r2", ("different", "uncertain", "different", "different")),
        _row("r3", ("similar", "similar", "different", "different")),
    ])
    quality = result["data_quality"]
    assert quality["two_plus_review_pair_count"] == 1
    assert quality["axis_quality"]["rhythm"]["disagreement"] == 1
    assert quality["axis_quality"]["rhythm"]["decisive_pair_count"] == 1
    assert quality["axis_quality"]["rhythm"]["decisive_agreement_fraction"] == 0.0
    assert quality["axis_quality"]["melody"]["unresolved"] == 1
    assert quality["axis_quality"]["melody"]["decisive_pair_count"] == 0
    assert quality["axis_quality"]["melody"]["decisive_agreement_fraction"] is None
    assert quality["axis_quality"]["instrumentation"]["unanimous_different"] == 1
    assert quality["axis_quality"]["instrumentation"]["decisive_agreement_fraction"] == 1.0
    assert quality["axis_quality"]["structure"]["unanimous_different"] == 1
    assert result["calibrated"] is False


def test_quality_uses_audio_pairs_not_number_of_review_rows(report: dict) -> None:
    report["coverage"]["C"] = {"sha256": "c" * 64, "unknown": {}}
    report["pairs"].extend([
        {
            "left": "A", "right": "C",
            "metrics": {
                "rhythm_onset_proxy": 0.8, "pitch_class_motion_proxy": 0.82,
                "energy_arc_proxy": 0.72,
            },
        },
        {
            "left": "B", "right": "C",
            "metrics": {
                "rhythm_onset_proxy": 0.83, "pitch_class_motion_proxy": 0.8,
                "energy_arc_proxy": None,
            },
        },
    ])
    first = _row("r1")
    first["right_id"] = "C"
    first["right_sha256"] = "c" * 64
    second = {**first, "reviewer_id": "r2"}
    third = {**first, "reviewer_id": "r3"}
    first_pair = [_row("r1"), _row("r2")]
    result = validate_listening_reviews(report, first_pair + [first, second, third])
    quality = result["data_quality"]
    assert result["pair_count"] == 3
    assert quality["two_plus_review_pair_count"] == 2
    assert quality["no_review_pair_count"] == 1
    assert quality["metric_available_pair_count"] == {
        "rhythm_onset_proxy": 3,
        "pitch_class_motion_proxy": 3,
        "energy_arc_proxy": 1,
    }
    assert quality["unique_source_sha256_count"] == 3
    assert quality["unique_different_sha256_comparison_count"] == 3
    assert quality["repeated_different_sha256_comparison_group_count"] == 0
    assert quality["axis_quality"]["rhythm"]["unanimous_similar"] == 2
    assert quality["axis_quality"]["rhythm"]["insufficient_reviewers"] == 1
    assert quality["axis_quality"]["rhythm"]["decisive_pair_count"] == 2
    assert quality["axis_quality"]["rhythm"]["decisive_agreement_fraction"] == 1.0
    assert quality["axis_quality"]["rhythm"]["unique_sha256_comparison_decisive_pair_count"] == 2
    assert quality["axis_quality"]["rhythm"]["unique_sha256_comparison_agreement_fraction"] == 1.0
    assert "r3" not in json.dumps(result)


def test_reused_audio_checksum_does_not_inflate_distinct_source_agreement(
    report: dict,
) -> None:
    # A and B are the identical file under two aliases. C has different bytes.
    report["coverage"]["B"] = {"sha256": "a" * 64, "unknown": {}}
    report["coverage"]["C"] = {"sha256": "c" * 64, "unknown": {}}
    report["pairs"][0]["metrics"] = dict.fromkeys((
        "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
    ), 1.0)
    for left, right in (("A", "C"), ("B", "C")):
        report["pairs"].append({
            "left": left, "right": right,
            "metrics": {
                "rhythm_onset_proxy": 0.8,
                "pitch_class_motion_proxy": 0.7,
                "energy_arc_proxy": 0.9,
            },
        })

    def row(left: str, right: str, reviewer: str, rhythm: str) -> dict[str, str]:
        return {
            **_row(reviewer),
            "left_id": left, "right_id": right,
            "left_sha256": report["coverage"][left]["sha256"],
            "right_sha256": report["coverage"][right]["sha256"],
            "rhythm_label": rhythm,
        }

    reviews = [
        row("A", "B", "r1", "similar"),
        row("A", "B", "r2", "similar"),
        row("A", "C", "r1", "similar"),
        row("A", "C", "r2", "different"),
    ]
    result = validate_listening_reviews(report, reviews)
    quality = result["data_quality"]
    assert quality["unique_source_sha256_count"] == 2
    assert quality["sha256_reused_by_multiple_aliases_count"] == 1
    assert quality["exact_same_sha256_pair_count"] == 1
    assert quality["different_sha256_pair_count"] == 2
    assert quality["max_different_sha256_pairs_sharing_one_source"] == 2
    assert quality["unique_different_sha256_comparison_count"] == 0
    assert quality["repeated_different_sha256_comparison_group_count"] == 1
    assert result["pairs"][0]["exact_same_sha256"] is True
    assert result["pairs"][1]["exact_same_sha256"] is False
    assert all(
        not pair["unique_different_sha256_comparison"] for pair in result["pairs"]
    )
    rhythm = quality["axis_quality"]["rhythm"]
    assert rhythm["decisive_pair_count"] == 2
    assert rhythm["decisive_agreement_fraction"] == 0.5
    assert rhythm["different_sha256_decisive_pair_count"] == 1
    assert rhythm["different_sha256_agreement_fraction"] == 0.0
    assert rhythm["unique_sha256_comparison_decisive_pair_count"] == 0
    assert rhythm["unique_sha256_comparison_agreement_fraction"] is None
    assert "a" * 64 not in json.dumps(result)
    assert result["calibrated"] is False


def test_reviewer_concentration_and_repeated_byte_pair_assignments(report: dict) -> None:
    empty = validate_listening_reviews(report, [])["data_quality"]
    assert empty["unique_reviewer_pseudonym_count"] == 0
    assert empty["max_reviewed_pairs_per_reviewer"] == 0
    assert empty["repeated_sha256_comparison_reviewer_count"] == 0

    # B is another name for A. The same r1 reviews both A-C and B-C.
    report["coverage"]["B"] = {"sha256": "a" * 64, "unknown": {}}
    report["coverage"]["C"] = {"sha256": "c" * 64, "unknown": {}}
    report["pairs"][0]["metrics"] = dict.fromkeys((
        "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
    ), 1.0)
    for left, right in (("A", "C"), ("B", "C")):
        report["pairs"].append({
            "left": left, "right": right,
            "metrics": {
                "rhythm_onset_proxy": 0.8,
                "pitch_class_motion_proxy": 0.7,
                "energy_arc_proxy": 0.9,
            },
        })

    def rated(left: str, right: str, reviewer: str) -> dict[str, str]:
        return {
            **_row(reviewer),
            "left_id": left, "right_id": right,
            "left_sha256": report["coverage"][left]["sha256"],
            "right_sha256": report["coverage"][right]["sha256"],
        }

    rows = [
        rated("A", "B", "r1"), rated("A", "B", "r2"),
        rated("A", "C", "r1"), rated("B", "C", "r1"),
        rated("B", "C", "r3"),
    ]
    result = validate_listening_reviews(report, rows)
    quality = result["data_quality"]
    assert quality["unique_reviewer_pseudonym_count"] == 3
    assert quality["max_reviewed_pairs_per_reviewer"] == 3
    assert quality["reviewers_covering_multiple_pairs_count"] == 1
    assert quality["repeated_sha256_comparison_reviewer_count"] == 1
    assert quality["extra_reviews_same_sha256_comparison_by_reviewer"] == 1
    assert result["reviewed_pair_count"] == 3
    assert "r1" not in json.dumps(result)
    assert "r2" not in json.dumps(result)
    assert "r3" not in json.dumps(result)


def test_reviewer_overlap_counts_shared_source_comparisons(report: dict) -> None:
    empty = validate_listening_reviews(report, [])["data_quality"]["reviewer_overlap"]
    assert empty["reviewer_pairs_with_shared_source_comparisons_count"] == 0
    assert empty["axis_co_ratings"]["rhythm"]["descriptive_agreement_fraction"] is None
    report["coverage"]["C"] = {"sha256": "c" * 64, "unknown": {}}
    report["pairs"].extend([
        {"left": "A", "right": "C", "metrics": {
            "rhythm_onset_proxy": 0.8, "pitch_class_motion_proxy": 0.8,
            "energy_arc_proxy": 0.8,
        }},
        {"left": "B", "right": "C", "metrics": {
            "rhythm_onset_proxy": 0.8, "pitch_class_motion_proxy": 0.8,
            "energy_arc_proxy": None,
        }},
    ])

    def rated(left: str, right: str, reviewer: str) -> dict[str, str]:
        return {
            **_row(reviewer), "left_id": left, "right_id": right,
            "left_sha256": report["coverage"][left]["sha256"],
            "right_sha256": report["coverage"][right]["sha256"],
        }

    rows = [
        rated("A", "B", "r1"), rated("A", "B", "r2"), rated("A", "B", "r3"),
        rated("A", "C", "r1"), rated("A", "C", "r2"),
    ]
    result = validate_listening_reviews(report, rows)
    overlap = result["data_quality"]["reviewer_overlap"]
    assert overlap["different_sha256_comparisons_with_two_plus_reviewers_count"] == 2
    assert overlap["reviewer_pairs_with_shared_source_comparisons_count"] == 3
    assert overlap["reviewer_pairs_with_two_plus_shared_source_comparisons_count"] == 1
    assert overlap["max_shared_source_comparisons_per_reviewer_pair"] == 2
    assert overlap["axis_co_ratings"]["rhythm"]["decisive_co_rating_count"] == 4
    assert overlap["axis_co_ratings"]["rhythm"]["descriptive_agreement_fraction"] == 1.0
    assert "r1" not in json.dumps(result)
    assert result["calibrated"] is False


def test_reviewer_overlap_excludes_alias_repeats_and_marks_conflicts(report: dict) -> None:
    report["coverage"]["B"] = {"sha256": "a" * 64, "unknown": {}}
    report["coverage"]["C"] = {"sha256": "c" * 64, "unknown": {}}
    report["pairs"][0]["metrics"] = dict.fromkeys((
        "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
    ), 1.0)
    for left, right in (("A", "C"), ("B", "C")):
        report["pairs"].append({
            "left": left, "right": right,
            "metrics": dict.fromkeys((
                "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
            ), 0.8),
        })

    def rated(left: str, right: str, reviewer: str, rhythm: str) -> dict[str, str]:
        return {
            **_row(reviewer), "left_id": left, "right_id": right,
            "left_sha256": report["coverage"][left]["sha256"],
            "right_sha256": report["coverage"][right]["sha256"],
            "rhythm_label": rhythm,
        }

    rows = [
        rated("A", "C", "r1", "similar"), rated("B", "C", "r1", "different"),
        rated("A", "C", "r2", "similar"), rated("B", "C", "r2", "similar"),
        rated("A", "B", "r1", "similar"), rated("A", "B", "r2", "similar"),
    ]
    result = validate_listening_reviews(report, rows)
    overlap = result["data_quality"]["reviewer_overlap"]
    assert overlap["different_sha256_comparisons_with_two_plus_reviewers_count"] == 1
    assert overlap["reviewer_pairs_with_shared_source_comparisons_count"] == 1
    assert overlap["max_shared_source_comparisons_per_reviewer_pair"] == 1
    assert overlap["conflicting_alias_label_groups_by_axis"]["rhythm"] == 1
    assert overlap["axis_co_ratings"]["rhythm"]["ambiguous_or_uncertain"] == 1
    assert overlap["axis_co_ratings"]["rhythm"]["decisive_co_rating_count"] == 0
    assert overlap["axis_co_ratings"]["rhythm"]["descriptive_agreement_fraction"] is None
    assert overlap["axis_co_ratings"]["instrumentation"]["agreement"] == 1
    assert "r1" not in json.dumps(result)


def test_operator_source_references_are_presence_only_and_not_verified(
    report: dict,
) -> None:
    empty = validate_listening_reviews(report, [])["data_quality"]["operator_source_reference"]
    assert all(value == 0 for value in empty.values())
    with_reference = {**_row("reviewer1"), "same_recording_source": "private-case-007"}
    blank = {**_row("reviewer2"), "same_recording_source": "  \t "}
    result = validate_listening_reviews(report, [with_reference, blank])
    quality = result["data_quality"]["operator_source_reference"]
    assert quality == {
        "nonempty_reference_review_row_count": 1,
        "reviewed_sha256_comparison_count": 1,
        "sha256_comparisons_with_nonempty_reference_count": 1,
        "sha256_comparisons_with_mixed_reference_presence_count": 1,
        "reviewer_sha256_comparison_groups_with_mixed_reference_presence_count": 0,
        "identical_sha256_review_rows_with_nonempty_reference_count": 0,
    }
    emitted = json.dumps(result)
    assert "private-case-007" not in emitted
    assert "reviewer1" not in emitted
    assert result["provenance_independently_verified"] is False
    assert result["rights_verified"] is False


def test_operator_source_reference_alias_variation_does_not_count_new_sources(
    report: dict,
) -> None:
    report["coverage"]["B"] = {"sha256": "a" * 64, "unknown": {}}
    report["coverage"]["C"] = {"sha256": "c" * 64, "unknown": {}}
    report["pairs"][0]["metrics"] = dict.fromkeys((
        "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
    ), 1.0)
    for left, right in (("A", "C"), ("B", "C")):
        report["pairs"].append({
            "left": left, "right": right,
            "metrics": dict.fromkeys((
                "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
            ), 0.8),
        })

    def rated(left: str, right: str, reviewer: str, reference: str) -> dict[str, str]:
        return {
            **_row(reviewer), "left_id": left, "right_id": right,
            "left_sha256": report["coverage"][left]["sha256"],
            "right_sha256": report["coverage"][right]["sha256"],
            "same_recording_source": reference,
        }

    result = validate_listening_reviews(report, [
        rated("A", "C", "r1", "private-source-proof"),
        rated("B", "C", "r1", "  "),
        rated("A", "C", "r2", "private-origin-note"),
        rated("B", "C", "r2", "another-reference"),
        rated("A", "B", "r1", "same-byte-note"),
    ])
    quality = result["data_quality"]["operator_source_reference"]
    assert quality == {
        "nonempty_reference_review_row_count": 4,
        "reviewed_sha256_comparison_count": 2,
        "sha256_comparisons_with_nonempty_reference_count": 2,
        "sha256_comparisons_with_mixed_reference_presence_count": 1,
        "reviewer_sha256_comparison_groups_with_mixed_reference_presence_count": 1,
        "identical_sha256_review_rows_with_nonempty_reference_count": 1,
    }
    emitted = json.dumps(result)
    assert "private-source-proof" not in emitted
    assert "private-origin-note" not in emitted
    assert "another-reference" not in emitted
    assert "same-byte-note" not in emitted
    assert result["rights_verified"] is False


def test_only_same_sha256_reviews_keep_distinct_source_agreement_unknown(
    report: dict,
) -> None:
    report["coverage"]["B"] = {"sha256": "a" * 64, "unknown": {}}
    report["pairs"][0]["metrics"] = dict.fromkeys((
        "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
    ), 1.0)
    rows = [
        {**_row("r1"), "right_sha256": "a" * 64},
        {**_row("r2"), "right_sha256": "a" * 64},
    ]
    result = validate_listening_reviews(report, rows)
    quality = result["data_quality"]
    assert quality["exact_same_sha256_pair_count"] == 1
    assert quality["different_sha256_pair_count"] == 0
    assert quality["max_different_sha256_pairs_sharing_one_source"] == 0
    assert quality["unique_different_sha256_comparison_count"] == 0
    assert quality["repeated_different_sha256_comparison_group_count"] == 0
    rhythm = quality["axis_quality"]["rhythm"]
    assert rhythm["decisive_agreement_fraction"] == 1.0
    assert rhythm["different_sha256_decisive_pair_count"] == 0
    assert rhythm["different_sha256_agreement_fraction"] is None
    assert result["pairs"][0]["exact_same_sha256"] is True
    assert result["rights_verified"] is False


def test_same_sha256_aliases_reject_conflicting_unknown_reasons(report: dict) -> None:
    report["coverage"]["B"]["sha256"] = "a" * 64
    # A and B claim the same bytes but B claims energy is unavailable.
    with pytest.raises(ValueError, match="report_same_source_unknown_conflict"):
        validate_listening_reviews(report, [])


def test_same_sha256_aliases_reject_nonself_similarity(report: dict) -> None:
    report["coverage"]["B"] = {"sha256": "a" * 64, "unknown": {}}
    report["pairs"][0]["metrics"]["energy_arc_proxy"] = 1.0
    with pytest.raises(ValueError, match="report_identical_source_similarity_conflict"):
        validate_listening_reviews(report, [])


def test_same_sha256_aliases_may_share_an_unknown_metric(report: dict) -> None:
    report["coverage"]["B"] = {
        "sha256": "a" * 64, "unknown": {"energy_arc_proxy": "no_measured_energy"},
    }
    report["coverage"]["A"]["unknown"] = {"energy_arc_proxy": "no_measured_energy"}
    report["pairs"][0]["metrics"] = {
        "rhythm_onset_proxy": 1.0,
        "pitch_class_motion_proxy": 1.0,
        "energy_arc_proxy": None,
    }
    result = validate_listening_reviews(report, [])
    assert result["pairs"][0]["exact_same_sha256"] is True
    assert result["pairs"][0]["metric_available"]["energy_arc_proxy"] is False


def test_repeated_source_byte_pairs_reject_different_scores(report: dict) -> None:
    report["coverage"]["B"] = {"sha256": "a" * 64, "unknown": {}}
    report["coverage"]["C"] = {"sha256": "c" * 64, "unknown": {}}
    report["pairs"][0]["metrics"] = dict.fromkeys((
        "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
    ), 1.0)
    for left, right in (("A", "C"), ("B", "C")):
        report["pairs"].append({
            "left": left, "right": right,
            "metrics": dict.fromkeys((
                "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
            ), 0.8),
        })
    assert validate_listening_reviews(report, [])["pair_count"] == 3
    report["pairs"][2]["metrics"]["rhythm_onset_proxy"] = 0.9
    with pytest.raises(ValueError, match="report_source_alias_metric_conflict"):
        validate_listening_reviews(report, [])


def test_disagreement_and_uncertainty_preserved(report: dict) -> None:
    output = validate_listening_reviews(report, [
        _row("r1"),
        _row("r2", ("different", "uncertain", "different", "different")),
    ])
    statuses = output["pairs"][0]["axis_status"]
    assert statuses == {
        "rhythm": "disagreement",
        "melody": "unresolved",
        "instrumentation": "unanimous",
        "structure": "unanimous",
    }
    assert output["complete_consensus_pair_count"] == 0


@pytest.mark.parametrize(("mutation", "message"), [
    ({"left_sha256": "0" * 64}, "review_checksum_mismatch"),
    ({"policy_version": "p6-offline-contrast-v2"}, "review_policy_mismatch"),
    ({"right_id": "missing"}, "review_unknown_pair"),
    ({"melody_label": ""}, "review_label_invalid"),
    ({"rhythm_label": "great"}, "review_label_invalid"),
    ({"reviewer_id": "someone@example.com"}, "reviewer_pseudonym_invalid"),
    ({"notes": "a" * 501}, "review_notes_invalid"),
])
def test_bad_review_row_rejected(
    report: dict, mutation: dict[str, str], message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_listening_reviews(report, [{**_row("r1"), **mutation}])


def test_duplicate_reviewer_even_on_reversed_pair_rejected(report: dict) -> None:
    reverse = {
        **_row("r1"), "left_id": "B", "right_id": "A",
        "left_sha256": "b" * 64, "right_sha256": "a" * 64,
    }
    with pytest.raises(ValueError, match="duplicate_reviewer"):
        validate_listening_reviews(report, [_row("r1"), reverse])


@pytest.mark.parametrize("bad", [float("nan"), 4.0, -1.0, True, "0.8"])
def test_bad_report_metrics_refused(report: dict, bad: object) -> None:
    report["pairs"][0]["metrics"]["rhythm_onset_proxy"] = bad
    with pytest.raises(ValueError, match="report_invalid_metric"):
        validate_listening_reviews(report, [_row("r1")])


def test_incomplete_report_and_version_refused(report: dict) -> None:
    other = copy.deepcopy(report)
    other["pairs"] = []
    with pytest.raises(ValueError, match="report_incomplete_pairs"):
        validate_listening_reviews(other, [])
    other = copy.deepcopy(report)
    other["policy_version"] = "p6-offline-contrast-v2"
    with pytest.raises(ValueError, match="report_policy_version_mismatch"):
        validate_listening_reviews(other, [])


def test_bad_headers_and_duplicate_rows_refused(report: dict) -> None:
    row = _row("r1")
    with pytest.raises(ValueError, match="review_columns_invalid"):
        validate_listening_reviews(report, [{**row, "extra": ""}])
    with pytest.raises(ValueError, match="duplicate_reviewer"):
        validate_listening_reviews(report, [row, row])


@pytest.mark.parametrize(("metric", "source", "value", "reason"), [
    ("rhythm_onset_proxy", "A", None, "report_metric_coverage_conflict"),
    ("energy_arc_proxy", "B", {}, "report_metric_coverage_conflict"),
    ("rhythm_onset_proxy", "A", {"rhythm_onset_proxy": "not_measured"},
     "report_metric_coverage_conflict"),
])
def test_report_metric_coverage_cannot_disagree(
    report: dict, metric: str, source: str, value: object, reason: str,
) -> None:
    if metric == "energy_arc_proxy":
        report["coverage"][source]["unknown"] = value
    elif value is None:
        report["pairs"][0]["metrics"][metric] = value
    else:
        report["coverage"][source]["unknown"] = value
    with pytest.raises(ValueError, match=reason):
        validate_listening_reviews(report, [])


@pytest.mark.parametrize("unknown", [
    None, "silent", {"rhythm_onset_proxy": ""}, {"made_up_metric": "silent"},
    {"rhythm_onset_proxy": 3},
])
def test_report_unknown_metadata_requires_valid_reasons(
    report: dict, unknown: object,
) -> None:
    report["coverage"]["A"]["unknown"] = unknown
    with pytest.raises(ValueError, match="report_invalid_unknown"):
        validate_listening_reviews(report, [])


def test_invalid_alias_control_character_rejected(report: dict) -> None:
    report["coverage"]["A\nsecret"] = report["coverage"].pop("A")
    with pytest.raises(ValueError, match="report_invalid_coverage"):
        validate_listening_reviews(report, [])


def test_nonstring_review_label_and_nonmapping_row_rejected(report: dict) -> None:
    with pytest.raises(ValueError, match="review_label_invalid"):
        validate_listening_reviews(report, [{**_row("r1"), "melody_label": ["similar"]}])
    with pytest.raises(ValueError, match="review_columns_invalid"):
        validate_listening_reviews(report, [None])


@pytest.mark.parametrize("json_problem", ["duplicate_key", "nan_constant"])
def test_cli_rejects_ambiguous_json_without_leaking_inputs(
    report: dict, tmp_path, capsys, json_problem: str,
) -> None:
    source, reviews = tmp_path / "report.json", tmp_path / "reviews.csv"
    if json_problem == "duplicate_key":
        # Even a repeated identical policy key would make a report ambiguous.
        raw = json.dumps(report).replace(
            '"policy_version":', '"policy_version":"opaque-user-secret","policy_version":',
            1,
        )
    else:
        report["untrusted_extra"] = float("nan")
        raw = json.dumps(report)
    source.write_text(raw, encoding="utf-8")
    reviews.write_text(",".join(REVIEW_COLUMNS) + "\n", encoding="utf-8")
    assert validate_album_listening_reviews.main([
        "--report", str(source), "--reviews", str(reviews),
    ]) == 2
    combined = capsys.readouterr()
    assert "opaque-user-secret" not in combined.err
    assert combined.out == ""


def test_cli_rejects_unterminated_quoted_csv(
    report: dict, tmp_path, capsys,
) -> None:
    source, reviews = tmp_path / "report.json", tmp_path / "reviews.csv"
    source.write_text(json.dumps(report), encoding="utf-8")
    reviews.write_text(
        ",".join(REVIEW_COLUMNS) + "\n" + '"unterminated private reviewer note',
        encoding="utf-8",
    )
    assert validate_album_listening_reviews.main([
        "--report", str(source), "--reviews", str(reviews),
    ]) == 2
    combined = capsys.readouterr()
    assert "private reviewer note" not in combined.err
    assert combined.out == ""


def test_cli_validates_without_echoing_reviewer_data(
    report: dict, tmp_path, capsys,
) -> None:
    report_path = tmp_path / "report.json"
    rows_path = tmp_path / "reviews.csv"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    raw = io.StringIO()
    writer = csv.DictWriter(raw, fieldnames=list(REVIEW_COLUMNS))
    writer.writeheader()
    writer.writerow(_row("r1"))
    writer.writerow(_row("r2"))
    rows_path.write_text(raw.getvalue(), encoding="utf-8")
    before = rows_path.read_bytes()
    assert validate_album_listening_reviews.main([
        "--report", str(report_path), "--reviews", str(rows_path),
    ]) == 0
    emitted = capsys.readouterr().out
    result = json.loads(emitted)
    assert result["complete_consensus_pair_count"] == 1
    assert "r1" not in emitted
    assert "fictional private observations" not in emitted
    assert rows_path.read_bytes() == before


def test_cli_refuses_invalid_report_without_leaking_input(
    report: dict, tmp_path, capsys,
) -> None:
    json_path, csv_path = tmp_path / "bad.json", tmp_path / "reviews.csv"
    json_path.write_text("{malicious payload token}", encoding="utf-8")
    csv_path.write_text(",".join(REVIEW_COLUMNS) + "\n", encoding="utf-8")
    assert validate_album_listening_reviews.main([
        "--report", str(json_path), "--reviews", str(csv_path),
    ]) == 2
    assert "malicious payload token" not in capsys.readouterr().err

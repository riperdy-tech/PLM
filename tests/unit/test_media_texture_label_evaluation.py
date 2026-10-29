"""Bounded human-labelled score validation; synthetic fixtures are not accuracy evidence."""
from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path

import pytest
from media_factory.audio.texture_label_evaluation import (
    EVALUATION,
    POLICY_VERSION,
    REVIEW_COLUMNS,
    evaluate_labeled_pair_scores,
)

from scripts.evaluate_p6_texture_labels import main

A, B, C, D = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)


def _report() -> dict[str, object]:
    return {
        "policy_version": POLICY_VERSION,
        "evaluation": EVALUATION,
        "pairs": [
            {
                "left_sha256": A, "right_sha256": B,
                "fixed_score": 0.7, "best_offset_score": 0.9,
                "best_rate_offset_score": 0.999,
            },
            {
                "left_sha256": C, "right_sha256": D,
                "fixed_score": 0.2, "best_offset_score": 0.5,
                "best_rate_offset_score": 0.999,
            },
        ],
    }


def _row(first: str, second: str, reviewer: str, label: str) -> dict[str, str]:
    return {
        "left_sha256": first, "right_sha256": second,
        "reviewer_id": reviewer, "relationship": label,
        "evidence_ref": "offline-reference-1",
    }


def test_missing_human_reviews_never_become_true_negatives() -> None:
    output = evaluate_labeled_pair_scores(_report(), [])
    assert output["source_pair_count"] == 2
    assert output["adjudicated_different_music_pairs"] == 0
    assert output["unadjudicated_pairs"] == 2
    assert output["operational_threshold_ready"] is False
    for scores in output["sample_confusion"].values():
        assert scores["sample_false_positive_fraction"] is None
        assert scores["sample_true_positive_fraction"] is None
        assert (scores["tp"], scores["fn"], scores["fp"], scores["tn"]) == (0, 0, 0, 0)


def test_two_reviewers_same_label_count_once_not_twice() -> None:
    rows = [
        _row(A, B, "listener_1", "same_recording"),
        _row(B, A, "listener_2", "same_recording"),
        _row(C, D, "listener_1", "different_music"),
        _row(D, C, "listener_2", "different_music"),
    ]
    output = evaluate_labeled_pair_scores(_report(), rows)
    assert output["adjudicated_same_recording_pairs"] == 1
    assert output["adjudicated_different_music_pairs"] == 1
    assert output["distinct_reviewer_pseudonyms_count"] == 2
    assert output["unadjudicated_pairs"] == 0
    old = output["sample_confusion"]["fixed_score"]
    new = output["sample_confusion"]["best_rate_offset_score"]
    assert (old["tp"], old["fn"], old["fp"], old["tn"]) == (0, 1, 0, 1)
    assert (new["tp"], new["fn"], new["fp"], new["tn"]) == (1, 0, 1, 0)
    assert new["sample_false_positive_fraction"] == 1.0
    assert new["sample_true_positive_fraction"] == 1.0
    assert "listener_1" not in json.dumps(output)
    assert "offline-reference-1" not in json.dumps(output)


@pytest.mark.parametrize(
    "rows",
    [
        [_row(A, B, "l1", "same_recording")],
        [_row(A, B, "l1", "same_recording"), _row(A, B, "l2", "uncertain")],
        [_row(A, B, "l1", "same_recording"), _row(A, B, "l2", "different_music")],
    ],
)
def test_incomplete_uncertain_disputed_labels_are_unadjudicated(
    rows: list[dict[str, str]],
) -> None:
    output = evaluate_labeled_pair_scores(_report(), rows)
    assert output["adjudicated_same_recording_pairs"] == 0
    assert output["unadjudicated_pairs"] == 2


@pytest.mark.parametrize(
    "bad_row",
    [
        {**_row(A, B, "l1", "same_recording"), "relationship": "probably_different"},
        {**_row(A, B, "l1", "different_music"), "evidence_ref": ""},
        {**_row(A, B, "l1", "same_recording"), "reviewer_id": "../bad"},
        {**_row(A, B, "l1", "same_recording"), "unexpected": "field"},
        _row(A, C, "l1", "same_recording"),
        _row(A, A, "l1", "same_recording"),
    ],
)
def test_invalid_or_unsupported_reviews_fail_closed(bad_row: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        evaluate_labeled_pair_scores(_report(), [bad_row])


def test_same_reviewer_cannot_vote_twice_by_reversing_pair() -> None:
    with pytest.raises(ValueError, match="duplicate_reviewer"):
        evaluate_labeled_pair_scores(
            _report(),
            [_row(A, B, "l1", "same_recording"),
             _row(B, A, "l1", "same_recording")],
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(policy_version="not-this-version"),
        lambda r: r.update(evaluation="approved"),
        lambda r: r["pairs"].append(dict(r["pairs"][0])),
        lambda r: r["pairs"][0].update(right_sha256=A),
        lambda r: r["pairs"][0].update(fixed_score=float("nan")),
        lambda r: r["pairs"][0].update(best_offset_score=True),
        lambda r: r["pairs"][0].update(best_rate_offset_score=1.01),
        lambda r: r["pairs"][0].update(best_offset_score=0.1),
        lambda r: r.update(pairs=[]),
    ],
)
def test_bad_or_inconsistent_score_reports_fail_closed(mutate: object) -> None:
    report = deepcopy(_report())
    mutate(report)
    with pytest.raises(ValueError):
        evaluate_labeled_pair_scores(report, [])


def test_command_accepts_report_without_labels_but_reports_no_accuracy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "scores.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    assert main(["--report", str(report)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["unadjudicated_pairs"] == 2
    assert output["sample_confusion"]["best_rate_offset_score"][
        "sample_false_positive_fraction"
    ] is None


def test_command_accepts_full_read_only_diagnostic_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "full-diagnostic.json"
    report.write_text(
        json.dumps({"unlabelled_pair_score_report": _report(), "store_writes": False}),
        encoding="utf-8",
    )
    assert main(["--report", str(report)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source_pair_count"] == 2
    assert output["unadjudicated_pairs"] == 2


def test_command_accepts_separate_human_csv_and_suppresses_identifiers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "scores.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    review = tmp_path / "labels.csv"
    with review.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerow(_row(A, B, "private_reviewer", "same_recording"))
        writer.writerow(_row(A, B, "another_reviewer", "same_recording"))
    assert main(["--report", str(report), "--reviews", str(review)]) == 0
    result = capsys.readouterr().out
    assert json.loads(result)["adjudicated_same_recording_pairs"] == 1
    assert "private_reviewer" not in result


def test_command_rejects_duplicate_json_key_and_bad_csv_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "scores.json"
    report.write_text('{"policy_version": "x", "policy_version": "x"}', encoding="utf-8")
    assert main(["--report", str(report)]) == 2
    report.write_text(json.dumps(_report()), encoding="utf-8")
    review = tmp_path / "labels.csv"
    review.write_text("left_sha256,right_sha256\\n", encoding="utf-8")
    assert main(["--report", str(report), "--reviews", str(review)]) == 2
    result = capsys.readouterr()
    assert "Texture label evaluation refused" in result.err
    assert str(tmp_path) not in result.err

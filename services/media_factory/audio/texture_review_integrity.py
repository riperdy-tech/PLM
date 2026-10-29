"""Fail-closed binding of a saved P6 listening queue to its archived source metadata.

A JSON checksum string by itself does not authenticate the report. The caller
must independently load selected P6 playlist metadata in READ ONLY mode and
re-fetch original source bytes with checksum verification before accepting scores.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from ..texture_sample_audit import MeasuredSample, SampleSource
from .texture_alignment import PLAYBACK_RATES, offset_aligned_texture
from .texture_label_evaluation import (
    EVALUATION,
    POLICY_VERSION,
    SCORE_KEYS,
    evaluate_labeled_pair_scores,
)
from .texture_review_selection import select_review_pairs

MAX_REVIEW_PAIRS = 12
REPORT_FIELDS = frozenset({
    "archived_tracks_in_selected_albums", "checksum_verified_sampled_tracks",
    "human_labels_supplied", "release_gate_changed", "review_queue",
    "scope", "selected_albums", "selected_review_pairs", "selection",
    "source_bytes", "source_metadata_transaction", "stored_audio_or_db_changes",
    "unlabelled_pair_score_report",
})
CASE_FIELDS = frozenset({
    "human_label", "left_package_id", "left_sha256", "left_song_id",
    "prescreen_fixed_score", "relative_prescreen_band",
    "right_package_id", "right_sha256", "right_song_id",
})
BANDS = frozenset({"high_relative", "middle_relative", "low_relative"})


def score_pair(first: bytes, second: bytes) -> tuple[float, float, float]:
    """Maximize over both directions for each bounded comparison method."""
    directions: list[tuple[float, float, float]] = []
    for reference, candidate in ((first, second), (second, first)):
        shifted = offset_aligned_texture(reference, candidate)
        combined = offset_aligned_texture(
            reference, candidate, playback_rates=PLAYBACK_RATES
        )
        directions.append((
            shifted.baseline_score, shifted.best_offset_score, combined.best_offset_score,
        ))
    return (
        max(item[0] for item in directions),
        max(item[1] for item in directions),
        max(item[2] for item in directions),
    )


def verify_review_queue_selection(
    report: object,
    measured: Sequence[MeasuredSample],
    bound: Sequence[tuple[SampleSource, SampleSource, tuple[float, float, float]]],
) -> None:
    """Replay the source-only stratification to detect queue/order manipulation.

    Requires real SHA256-verified audio features, independently extracted by the
    caller; saved prescreen scores or strata are not treated as source evidence.
    """
    if not isinstance(report, dict) or not isinstance(report.get("review_queue"), list):
        raise TypeError("invalid_queue_for_selection_replay")
    cases = report["review_queue"]
    if not bound or len(cases) != len(bound):
        raise ValueError("selection_replay_pair_count_mismatch")
    replayed = select_review_pairs(measured, max_pairs=len(bound))
    if len(replayed) != len(bound):
        raise ValueError("selection_replay_pair_count_mismatch")
    for position, (actual, (left, right, _scores)) in enumerate(
        zip(replayed, bound, strict=True)
    ):
        if (
            (actual.left.package_id, actual.left.song_id, actual.left.sha256)
            != (left.package_id, left.song_id, left.sha256)
            or (actual.right.package_id, actual.right.song_id, actual.right.sha256)
            != (right.package_id, right.song_id, right.sha256)
            or cases[position]["relative_prescreen_band"] != actual.relative_score_band
            or not math.isclose(
                float(cases[position]["prescreen_fixed_score"]),
                actual.fixed_prescreen_score, rel_tol=0.0, abs_tol=1e-8,
            )
        ):
            raise ValueError("saved_queue_selection_does_not_reproduce")


def bind_review_queue_sources(
    report: object,
    sources: Sequence[SampleSource],
    *,
    selected_package_ids: Sequence[str],
    samples_per_album: int,
    archived_track_count: int,
) -> tuple[tuple[SampleSource, SampleSource, tuple[float, float, float]], ...]:
    """Verify exact queue schema and source identity against current P6 metadata.

    Only pre-existing, unlabelled diagnostic queues may be checked. The score
    tuples are operator claims and still require recomputation from original
    bytes; a successful return alone is never a score/authorship attestation.
    """
    if not isinstance(report, dict) or set(report) != REPORT_FIELDS:
        raise ValueError("invalid_review_queue_schema")
    ids = list(selected_package_ids)
    if (
        not 2 <= len(ids) <= 8 or len(set(ids)) != len(ids)
        or samples_per_album not in (2, 3)
    ):
        raise ValueError("invalid_explicit_package_selection")
    if (
        type(report["selected_albums"]) is not int
        or report["selected_albums"] != len(ids)
        or type(report["archived_tracks_in_selected_albums"]) is not int
        or report["archived_tracks_in_selected_albums"] != archived_track_count
        or type(report["checksum_verified_sampled_tracks"]) is not int
        or report["checksum_verified_sampled_tracks"] != len(sources)
        or len(sources) != len(ids) * samples_per_album
        or report["scope"] != "explicit_sha256_verified_p6_source_sample_review_selection"
        or report["selection"] != "relative_spectral_strata_not_random_accuracy_sample"
        or report["source_bytes"] != "checksum_verified_minio_reads"
        or report["source_metadata_transaction"] != "repeatable_read_read_only"
        or report["human_labels_supplied"] != 0
        or report["stored_audio_or_db_changes"] is not False
        or report["release_gate_changed"] is not False
    ):
        raise ValueError("review_queue_metadata_mismatch")
    cases = report["review_queue"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_REVIEW_PAIRS:
        raise ValueError("invalid_review_queue_length")
    if type(report["selected_review_pairs"]) is not int or (
        report["selected_review_pairs"] != len(cases)
    ):
        raise ValueError("review_queue_count_mismatch")
    scored = report["unlabelled_pair_score_report"]
    if not isinstance(scored, dict) or set(scored) != {
        "policy_version", "evaluation", "pairs",
    } or scored["policy_version"] != POLICY_VERSION or scored["evaluation"] != EVALUATION:
        raise ValueError("invalid_review_score_envelope")
    score_rows = scored["pairs"]
    if not isinstance(score_rows, list) or len(score_rows) != len(cases):
        raise ValueError("review_score_pair_count_mismatch")
    # Reuse the existing strict score validator, without inferring any human label.
    evaluate_labeled_pair_scores(scored, [])

    available = {
        (src.package_id, src.song_id, src.sha256): src
        for src in sources if src.package_id in ids
    }
    if len(available) != len(sources):
        raise ValueError("nonunique_or_unselected_source_metadata")
    bound: list[tuple[SampleSource, SampleSource, tuple[float, float, float]]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for case, row in zip(cases, score_rows, strict=True):
        if (
            not isinstance(case, Mapping) or set(case) != CASE_FIELDS
            or case["human_label"] is not None
            or case["relative_prescreen_band"] not in BANDS
        ):
            raise ValueError("review_case_invalid_or_has_human_label")
        prescreen = case["prescreen_fixed_score"]
        if (
            type(prescreen) not in (float, int) or not math.isfinite(prescreen)
            or not 0 <= prescreen <= 1
        ):
            raise ValueError("invalid_prescreen_score")
        try:
            left = available[(
                case["left_package_id"], case["left_song_id"], case["left_sha256"],
            )]
            right = available[(
                case["right_package_id"], case["right_song_id"], case["right_sha256"],
            )]
        except (KeyError, TypeError) as exc:
            raise ValueError("review_case_source_not_in_archived_selection") from exc
        if left.release_id == right.release_id or left.sha256 == right.sha256:
            raise ValueError("review_case_not_distinct_cross_release_pair")
        key = (min(left.sha256, right.sha256), max(left.sha256, right.sha256))
        if key in seen_pairs:
            raise ValueError("duplicate_review_case_pair")
        seen_pairs.add(key)
        if (
            not isinstance(row, dict)
            or row["left_sha256"] != left.sha256
            or row["right_sha256"] != right.sha256
        ):
            raise ValueError("review_score_source_binding_mismatch")
        claimed = tuple(float(row[name]) for name in SCORE_KEYS)
        if not math.isclose(float(prescreen), claimed[0], rel_tol=0, abs_tol=1e-7):
            raise ValueError("review_prescreen_score_disagrees_with_fixed_score")
        bound.append((left, right, (claimed[0], claimed[1], claimed[2])))
    return tuple(bound)

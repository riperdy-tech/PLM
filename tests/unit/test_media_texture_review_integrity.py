"""Saved listening scores must be re-bound to archived metadata, never trusted alone."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Self

import pytest
from media_factory.audio.texture_label_evaluation import EVALUATION, POLICY_VERSION
from media_factory.audio.texture_review_integrity import (
    bind_review_queue_sources,
    verify_review_queue_selection,
)
from media_factory.audio.texture_review_selection import select_review_pairs
from media_factory.texture_sample_audit import MeasuredSample, SampleSource

import scripts.verify_p6_texture_review_queue as verifier


def _sources() -> tuple[SampleSource, ...]:
    return (
        SampleSource("p1", "r1", "s1", "mem://a", "a" * 64),
        SampleSource("p1", "r1", "s2", "mem://b", "b" * 64),
        SampleSource("p2", "r2", "s3", "mem://c", "c" * 64),
        SampleSource("p2", "r2", "s4", "mem://d", "d" * 64),
    )


def _report() -> dict[str, object]:
    cases = []
    scores = []
    for idx, (left, right) in enumerate(((_sources()[0], _sources()[2]),
                                         (_sources()[1], _sources()[3]))):
        cases.append({
            "left_package_id": left.package_id, "left_song_id": left.song_id,
            "left_sha256": left.sha256,
            "right_package_id": right.package_id, "right_song_id": right.song_id,
            "right_sha256": right.sha256,
            "relative_prescreen_band": ("high_relative", "low_relative")[idx],
            "prescreen_fixed_score": 0.2,
            "human_label": None,
        })
        scores.append({
            "left_sha256": left.sha256, "right_sha256": right.sha256,
            "fixed_score": 0.2, "best_offset_score": 0.3,
            "best_rate_offset_score": 0.4,
        })
    return {
        "archived_tracks_in_selected_albums": 38,
        "checksum_verified_sampled_tracks": 4,
        "human_labels_supplied": 0,
        "release_gate_changed": False,
        "review_queue": cases,
        "scope": "explicit_sha256_verified_p6_source_sample_review_selection",
        "selected_albums": 2,
        "selected_review_pairs": 2,
        "selection": "relative_spectral_strata_not_random_accuracy_sample",
        "source_bytes": "checksum_verified_minio_reads",
        "source_metadata_transaction": "repeatable_read_read_only",
        "stored_audio_or_db_changes": False,
        "unlabelled_pair_score_report": {
            "policy_version": POLICY_VERSION, "evaluation": EVALUATION,
            "pairs": scores,
        },
    }


def _bind(report: object, sources: tuple[SampleSource, ...] | None = None) -> object:
    return bind_review_queue_sources(
        report, sources if sources is not None else _sources(),
        selected_package_ids=("p1", "p2"),
        samples_per_album=2, archived_track_count=38,
    )


def test_exact_archived_queue_binds_without_authenticating_claimed_scores() -> None:
    bound = _bind(_report())
    assert len(bound) == 2
    assert bound[0][0].audio_uri == "mem://a"
    assert bound[0][1].audio_uri == "mem://c"
    assert bound[0][2] == (0.2, 0.3, 0.4)


@pytest.mark.parametrize("mutation", [
    lambda r: r["review_queue"][0].update(left_sha256="f" * 64),
    lambda r: r["review_queue"][0].update(right_song_id="unrelated"),
    lambda r: r["review_queue"][0].update(human_label="different_music"),
    lambda r: r["review_queue"][0].update(relative_prescreen_band="unknown"),
    lambda r: r["review_queue"][0].update(prescreen_fixed_score=0.21),
    lambda r: r["review_queue"][0].update(prescreen_fixed_score=float("nan")),
    lambda r: r["unlabelled_pair_score_report"]["pairs"][0].update(
        fixed_score=0.5),
    lambda r: r["unlabelled_pair_score_report"]["pairs"][0].update(
        left_sha256="e" * 64),
    lambda r: r.update(selected_review_pairs=3),
    lambda r: r.update(selected_albums=3),
    lambda r: r.update(human_labels_supplied=1),
    lambda r: r.update(release_gate_changed=True),
    lambda r: r.update(stored_audio_or_db_changes=True),
    lambda r: r.update(archived_tracks_in_selected_albums=37),
    lambda r: r.update(checksum_verified_sampled_tracks=3),
    lambda r: r.update(source_metadata_transaction="not_read_only"),
    lambda r: r["review_queue"].append(dict(r["review_queue"][0])),
])
def test_tampered_queue_or_score_does_not_bind(mutation: object) -> None:
    report = deepcopy(_report())
    mutation(report)
    with pytest.raises((ValueError, TypeError)):
        _bind(report)


def test_changed_archive_source_metadata_refuses() -> None:
    sources = _sources()
    moved = (
        SampleSource("p1", "r1", "s1", "mem://a", "f" * 64),
        *sources[1:],
    )
    with pytest.raises(ValueError, match="not_in_archived_selection"):
        _bind(_report(), moved)


def _selection_fixture() -> tuple[dict[str, object], tuple[MeasuredSample, ...]]:
    names = _sources()
    measured = []
    for index, source in enumerate(names):
        spectrum = [0.0] * 192
        for window in range(4):
            spectrum[window * 48 + index] = 1.0
        measured.append(MeasuredSample(
            source.package_id, source.release_id, source.song_id,
            source.sha256, tuple(spectrum),
        ))
    observed = tuple(measured)
    selected = select_review_pairs(observed, max_pairs=2)
    report = _report()
    cases = report["review_queue"]
    values = report["unlabelled_pair_score_report"]["pairs"]
    for case, score, chosen in zip(cases, values, selected, strict=True):
        case.update({
            "left_package_id": chosen.left.package_id,
            "left_song_id": chosen.left.song_id,
            "left_sha256": chosen.left.sha256,
            "right_package_id": chosen.right.package_id,
            "right_song_id": chosen.right.song_id,
            "right_sha256": chosen.right.sha256,
            "relative_prescreen_band": chosen.relative_score_band,
            "prescreen_fixed_score": chosen.fixed_prescreen_score,
        })
        score.update({
            "left_sha256": chosen.left.sha256,
            "right_sha256": chosen.right.sha256,
            "fixed_score": chosen.fixed_prescreen_score,
            "best_offset_score": max(0.3, chosen.fixed_prescreen_score),
            "best_rate_offset_score": max(0.4, chosen.fixed_prescreen_score),
        })
    return report, observed


def test_replay_reproduces_selection_and_rejects_altered_band_or_order() -> None:
    report, measured = _selection_fixture()
    bound = _bind(report)
    verify_review_queue_selection(report, measured, bound)
    report["review_queue"][0]["relative_prescreen_band"] = "low_relative" if (
        report["review_queue"][0]["relative_prescreen_band"] != "low_relative"
    ) else "high_relative"
    with pytest.raises(ValueError, match="selection_does_not_reproduce"):
        verify_review_queue_selection(report, measured, bound)
    report, measured = _selection_fixture()
    report["review_queue"].reverse()
    report["unlabelled_pair_score_report"]["pairs"].reverse()
    bound = _bind(report)
    with pytest.raises(ValueError, match="selection_does_not_reproduce"):
        verify_review_queue_selection(report, measured, bound)


def test_command_recomputes_verified_audio_and_returns_no_human_clearance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_file = tmp_path / "queue.json"
    input_file.write_text(json.dumps(_report()), encoding="utf-8")
    calls: list[str] = []

    class FakeEngine:
        def dispose(self) -> None:
            calls.append("dispose")

    class FakeSession:
        def __init__(self, _engine: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append("exit")

        def execute(self, statement: object) -> None:
            if "READ ONLY" not in str(statement):
                raise AssertionError("must be read-only")
            calls.append("read_only")

        def rollback(self) -> None:
            calls.append("rollback")

    class FakeStore:
        def __init__(self, _settings: object) -> None:
            pass

        def fetch_verified(self, uri: str, sha256: str) -> bytes:
            calls.append("fetch:"+uri+":"+sha256[:1])
            return sha256.encode()

        def put(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("write forbidden")

    monkeypatch.setattr(verifier, "load_settings", lambda: object())
    monkeypatch.setattr(verifier, "build_engine", lambda _settings: FakeEngine())
    monkeypatch.setattr(verifier, "Session", FakeSession)
    monkeypatch.setattr(verifier, "MinioArtifactStore", FakeStore)
    monkeypatch.setattr(
        verifier, "load_sample_sources",
        lambda _session, _ids, samples_per_album: (_sources(), 38),
    )
    monkeypatch.setattr(verifier, "score_pair", lambda _a, _b: (0.2, 0.3, 0.4))
    monkeypatch.setattr(
        verifier, "measure_sources",
        lambda store, sources: tuple(
            store.fetch_verified(source.audio_uri, source.sha256) for source in sources
        ),
    )
    monkeypatch.setattr(
        verifier, "verify_review_queue_selection",
        lambda _report, _measured, _bound: calls.append("selection_replayed"),
    )
    args = ["--report", str(input_file), "-p", "p1", "-p", "p2"]
    assert verifier.main(args) == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["status"] == "source_bound_scores_recomputed"
    assert outcome["validated_source_pair_count"] == 2
    assert outcome["original_sha256_verifications"] == 8
    assert outcome["selection_replayed_from_verified_audio"] is True
    assert outcome["human_review_or_rights_verified"] is False
    assert calls[:2] == ["read_only", "rollback"]
    assert calls.count("dispose") == 1
    assert calls.count("selection_replayed") == 1
    assert len([c for c in calls if c.startswith("fetch:")]) == 8

    monkeypatch.setattr(verifier, "score_pair", lambda _a, _b: (0.2, 0.3, 0.39))
    assert verifier.main(args) == 2
    error = capsys.readouterr().err
    assert "review_queue_unverifiable" in error
    assert "p1" not in error and str(input_file) not in error


def test_command_refuses_duplicate_json_keys_and_nonfinite_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "queue.json"
    report.write_text('{"scope":"x","scope":"y"}', encoding="utf-8")
    assert verifier.main(["--report", str(report), "-p", "p1", "-p", "p2"]) == 2
    report.write_text('{"score":NaN}', encoding="utf-8")
    assert verifier.main(["--report", str(report), "-p", "p1", "-p", "p2"]) == 2
    assert "review_queue_unverifiable" in capsys.readouterr().err

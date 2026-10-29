"""Unlabelled P6 review-queue selection never invents negative labels."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Self

import pytest
from media_factory.audio.texture_label_evaluation import evaluate_labeled_pair_scores
from media_factory.audio.texture_review_selection import select_review_pairs
from media_factory.texture_sample_audit import MeasuredSample, SampleSource

import scripts.prepare_p6_texture_review_queue as queue
from tests.support.media_audio import synth_track


def _sig(b: int) -> tuple[float, ...]:
    out = [0.0] * 192
    for window in range(4):
        out[48 * window + b] = 1.0
    return tuple(out)


def _tracks(albums: int = 8, each: int = 2) -> tuple[MeasuredSample, ...]:
    result = []
    for album in range(albums):
        for item in range(each):
            index = album * each + item
            result.append(MeasuredSample(
                f"pkg{album}", f"release{album}", f"song{index}",
                f"{index + 1:064x}", _sig((index * 5 + album) % 12),
            ))
    return tuple(result)


def test_balanced_strata_deterministic_across_input_order() -> None:
    source = _tracks()
    first = select_review_pairs(source, max_pairs=9)
    second = select_review_pairs(tuple(reversed(source)), max_pairs=9)
    assert first == second
    assert len(first) == 9
    assert {item.relative_score_band for item in first} == {
        "high_relative", "middle_relative", "low_relative"
    }
    assert len({tuple(sorted((p.left.sha256, p.right.sha256))) for p in first}) == 9
    assert all(p.left.release_id != p.right.release_id for p in first)


def test_two_album_selection_caps_at_available_cross_release_pairs() -> None:
    selected = select_review_pairs(_tracks(2), max_pairs=6)
    assert len(selected) == 4
    assert len({p.left.sha256 + p.right.sha256 for p in selected}) == 4


def test_duplicate_sha_or_unmeasurable_signature_fails_closed() -> None:
    examples = _tracks(2)
    with pytest.raises(ValueError, match="exact-reused"):
        select_review_pairs((*examples[:1], replace(examples[1], sha256=examples[0].sha256),
                             *examples[2:]))
    with pytest.raises(ValueError, match="invalid"):
        select_review_pairs((replace(examples[0], signature=(0.0,) * 192),
                             *examples[1:]))
    with pytest.raises(ValueError, match="invalid"):
        select_review_pairs((replace(examples[0], sha256="not-a-checksum"),
                             *examples[1:]))


def test_single_release_and_input_limits_fail_closed() -> None:
    inputs = _tracks(2)
    with pytest.raises(ValueError, match="packages"):
        select_review_pairs(tuple(replace(t, package_id="same") for t in inputs))
    with pytest.raises(ValueError, match="4..24"):
        select_review_pairs(inputs[:3])
    with pytest.raises(ValueError, match="2..12"):
        select_review_pairs(inputs, max_pairs=13)


def test_bounded_source_scores_are_nested() -> None:
    a = synth_track(seconds=7, bpm=108, tonic=3, minor=True)
    b = synth_track(seconds=7, bpm=132, tonic=9, minor=False)
    fixed, shifted, combined = queue.score_pair(a, b)
    assert 0 <= fixed <= shifted <= combined <= 1


def test_main_uses_read_only_metadata_and_returns_unlabelled_evaluable_scores(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    measured = _tracks(2)
    sources = tuple(SampleSource(
        sample.package_id, sample.release_id, sample.song_id,
        "mem://" + sample.sha256, sample.sha256,
    ) for sample in measured)
    calls: list[str] = []

    class FakeSession:
        def __init__(self, _engine: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append("session_exit")

        def execute(self, stmt: object) -> None:
            if "READ ONLY" not in str(stmt):
                raise AssertionError("metadata transaction not read-only")
            calls.append("read_only")

        def rollback(self) -> None:
            calls.append("rollback")

    class FakeEngine:
        def dispose(self) -> None:
            calls.append("dispose")

    class FakeStore:
        def __init__(self, _settings: object) -> None:
            pass

        def fetch_verified(self, _uri: str, _sha256: str) -> bytes:
            calls.append("checksum_verified_fetch")
            return b"verified-audio"

        def put(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("never write audio")

    monkeypatch.setattr(queue, "Session", FakeSession)
    monkeypatch.setattr(queue, "build_engine", lambda _settings: FakeEngine())
    monkeypatch.setattr(queue, "load_settings", lambda: object())
    monkeypatch.setattr(queue, "MinioArtifactStore", FakeStore)
    monkeypatch.setattr(
        queue, "load_sample_sources",
        lambda _session, _ids, samples_per_album: (sources, 38),
    )
    monkeypatch.setattr(queue, "measure_sources", lambda _store, _sources: measured)
    monkeypatch.setattr(queue, "score_pair", lambda _a, _b: (0.2, 0.3, 0.4))
    assert queue.main(["-p", "pkg0", "-p", "pkg1", "--max-pairs", "4"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["checksum_verified_sampled_tracks"] == 4
    assert output["selected_review_pairs"] == 4
    assert output["human_labels_supplied"] == 0
    assert all(case["human_label"] is None for case in output["review_queue"])
    assert calls.count("checksum_verified_fetch") == 8
    assert calls[:2] == ["read_only", "rollback"]
    assert "dispose" in calls
    evaluated = evaluate_labeled_pair_scores(output["unlabelled_pair_score_report"], [])
    assert evaluated["unadjudicated_pairs"] == 4
    assert evaluated["sample_confusion"]["best_rate_offset_score"][
        "sample_false_positive_fraction"
    ] is None

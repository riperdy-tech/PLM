"""P6 synthetic sensitivity probe; evidence of classifier limits, not live rights."""
from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf
from media_factory.audio.features import TrackFeatures

import scripts.diagnose_p6_texture_robustness as robustness
from scripts.diagnose_p6_texture_robustness import (
    audio_variants,
    profile_audio,
    profile_audio_offset_search,
    profile_unlabelled_cross_sources,
    texture_score,
)
from tests.support.media_audio import synth_track


@pytest.fixture(scope="module")
def tonal_clip() -> bytes:
    return synth_track(seconds=7, bpm=108, tonic=3, minor=True)


def test_deterministic_same_source_variants_are_changed_bytes(tonal_clip: bytes) -> None:
    first = list(audio_variants(tonal_clip))
    second = list(audio_variants(tonal_clip))
    assert [name for name, _ in first] == [
        "gain_0.65", "speed_1.05_pitch_shifted", "time_offset_0.5s"
    ]
    assert first == second
    assert all(audio != tonal_clip for _, audio in first)


def test_original_and_gain_changed_copy_are_both_highly_similar(tonal_clip: bytes) -> None:
    result = profile_audio(tonal_clip)
    assert len(result) == 3
    assert all(0 <= score <= 1 for score in result.values())
    assert result["gain_0.65"] >= 0.998


def test_baseline_feature_is_measured_only_once(
    tonal_clip: bytes, monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    underlying = robustness.measure_features

    def counted(candidate_id: str, audio: bytes) -> TrackFeatures:
        called.append(candidate_id)
        return underlying(candidate_id, audio)

    monkeypatch.setattr(robustness, "measure_features", counted)
    profile_audio(tonal_clip)
    assert called == [
        "original", "gain_0.65", "speed_1.05_pitch_shifted", "time_offset_0.5s"
    ]


def test_baseline_reuse_preserves_independent_comparison_scores(
    tonal_clip: bytes,
) -> None:
    actual = profile_audio(tonal_clip)
    for variant_name, modified in audio_variants(tonal_clip):
        assert actual[variant_name] == pytest.approx(
            texture_score(tonal_clip, modified), abs=1e-12
        )


def test_opt_in_playback_grid_reports_rate_without_changing_fixed_scores(
    tonal_clip: bytes,
) -> None:
    old = profile_audio_offset_search(tonal_clip)
    combined = profile_audio_offset_search(
        tonal_clip, search_playback_rates=True
    )
    assert set(combined) == set(old)
    for name in old:
        assert combined[name]["fixed_score"] == pytest.approx(
            old[name]["fixed_score"], abs=1e-12
        )
        assert combined[name]["best_rate_offset_score"] >= old[name]["best_offset_score"]
        assert 1 <= combined[name]["valid_rate_offset_pairs"] <= 21
    assert combined["speed_1.05_pitch_shifted"]["best_playback_rate"] == 1.05


def test_cross_source_diagnostic_has_no_inferred_song_label(
    tonal_clip: bytes,
) -> None:
    distinct = synth_track(seconds=7, bpm=132, tonic=9, minor=False)
    scores = profile_unlabelled_cross_sources(tonal_clip, distinct)
    assert set(scores) == {"first_to_second", "second_to_first"}
    for direction in scores.values():
        assert (
            0 <= direction["fixed_score"]
            <= direction["best_offset_score"]
            <= direction["best_rate_offset_score"] <= 1
        )
        assert direction["valid_rate_offset_pairs"] == 21
        assert direction["best_playback_rate"] in (0.95, 1.0, 1.05)


def test_texture_score_rejects_undecodable_variant(tonal_clip: bytes) -> None:
    with pytest.raises(ValueError, match="variant has no valid texture"):
        texture_score(tonal_clip, b"invalid WAV")


def test_variant_generator_refuses_audio_length_above_diagnostic_bound() -> None:
    generator = audio_variants(b"x" * (64 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="byte limit"):
        next(generator)


def test_variant_generator_refuses_audio_too_short() -> None:
    original = synth_track(seconds=1)
    with pytest.raises(ValueError, match="too short"):
        list(audio_variants(original))


def test_time_offset_inserts_silence_only_in_transient_buffer(tonal_clip: bytes) -> None:
    variants = dict(audio_variants(tonal_clip))
    original, rate = sf.read(io.BytesIO(tonal_clip), dtype="float32")
    shifted, new_rate = sf.read(io.BytesIO(variants["time_offset_0.5s"]), dtype="float32")
    assert rate == new_rate and original.shape == shifted.shape
    assert np.all(shifted[:round(rate * 0.5)] == 0)

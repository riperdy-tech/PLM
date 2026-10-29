"""Diagnostic-only offset-aligned P6 spectra; no release-gate changes."""
from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf
from media_factory.audio.texture_alignment import (
    OFFSETS_SECONDS,
    PLAYBACK_RATES,
    offset_aligned_texture,
)

from scripts.diagnose_p6_texture_robustness import audio_variants, texture_score
from tests.support.media_audio import synth_track


@pytest.fixture(scope="module")
def original() -> bytes:
    return synth_track(seconds=7, bpm=108, tonic=3, minor=True)


def test_zero_offset_matches_original_p6_score(original: bytes) -> None:
    for _, transformed in audio_variants(original):
        result = offset_aligned_texture(original, transformed)
        assert result.baseline_score == pytest.approx(
            texture_score(original, transformed), abs=1e-8
        )
        assert result.best_offset_score >= result.baseline_score - 1e-12
        assert 1 <= result.examined_offsets <= len(OFFSETS_SECONDS)


def test_leading_silence_can_be_aligned_without_updating_production_gate(
    original: bytes,
) -> None:
    displaced = dict(audio_variants(original))["time_offset_0.5s"]
    result = offset_aligned_texture(original, displaced)
    assert result.baseline_score < 0.998
    assert result.best_offset_score > result.baseline_score + 0.05
    assert result.best_candidate_shift_seconds in OFFSETS_SECONDS


def test_level_adjustment_remains_equivalent_after_alignment(original: bytes) -> None:
    gain = dict(audio_variants(original))["gain_0.65"]
    result = offset_aligned_texture(original, gain)
    assert result.baseline_score > 0.998
    assert result.best_offset_score > 0.998


def test_distinct_synthetic_key_is_not_evidence_of_calibrated_false_positive_rate(
    original: bytes,
) -> None:
    another = synth_track(seconds=7, bpm=132, tonic=9, minor=False)
    result = offset_aligned_texture(original, another)
    assert result.best_offset_score >= result.baseline_score
    assert result.best_offset_score < 0.998  # only this labelled-by-construction pair


@pytest.mark.parametrize(
    "invalid_offsets",
    [(), (0.5,), (0.0, 0.0), (0.0, 1.0), (0.0, float("nan")),
     (0.0, float("inf")), tuple(range(8))],
)
def test_rejects_unbounded_or_missing_reference_offset(
    original: bytes, invalid_offsets: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError, match="offsets"):
        offset_aligned_texture(original, original, offsets_seconds=invalid_offsets)


def test_rate_search_recovers_known_faster_pitch_shifted_copy(original: bytes) -> None:
    speedup = dict(audio_variants(original))["speed_1.05_pitch_shifted"]
    offset_only = offset_aligned_texture(original, speedup)
    combined = offset_aligned_texture(
        original, speedup, playback_rates=PLAYBACK_RATES
    )
    assert combined.baseline_score == pytest.approx(offset_only.baseline_score, abs=1e-12)
    assert combined.best_offset_score > offset_only.best_offset_score + 0.02
    assert combined.best_candidate_playback_rate == 1.05
    assert combined.examined_offsets <= len(OFFSETS_SECONDS) * len(PLAYBACK_RATES)


def test_default_search_never_changes_playback_rate(original: bytes) -> None:
    speedup = dict(audio_variants(original))["speed_1.05_pitch_shifted"]
    comparison = offset_aligned_texture(original, speedup)
    assert comparison.best_candidate_playback_rate == 1.0
    assert comparison.examined_offsets <= len(OFFSETS_SECONDS)


@pytest.mark.parametrize(
    "invalid_rates",
    [(), (1.05,), (1.0, 1.0), (1.0, 1.06), (1.0, 0.9),
     (1.0, float("nan")), (1.0, float("inf")), (1.0, True),
     (0.95, 1.0, 1.05, 1.01)],
)
def test_rejects_invalid_playback_rate_search(
    original: bytes, invalid_rates: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError, match="playback rates"):
        offset_aligned_texture(original, original, playback_rates=invalid_rates)


def test_refuses_undecodable_wav_and_too_short_audio(original: bytes) -> None:
    with pytest.raises(ValueError):
        offset_aligned_texture(original, b"not a WAV")
    with pytest.raises(ValueError, match="too short"):
        offset_aligned_texture(original, synth_track(seconds=1))


def test_refuses_incompatible_sample_rates(original: bytes) -> None:
    values, _rate = sf.read(io.BytesIO(original), always_2d=True)
    out = io.BytesIO()
    sf.write(out, values, 48000, format="WAV", subtype="PCM_16")
    with pytest.raises(ValueError, match="sample rates"):
        offset_aligned_texture(original, out.getvalue())


def test_refuses_silence_without_assigning_artificial_zero(original: bytes) -> None:
    values, rate = sf.read(io.BytesIO(original), always_2d=True)
    out = io.BytesIO()
    sf.write(out, np.zeros_like(values), rate, format="WAV", subtype="PCM_16")
    with pytest.raises(ValueError, match="texture"):
        offset_aligned_texture(original, out.getvalue())

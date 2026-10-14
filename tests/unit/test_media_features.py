"""P6 feature measurement from track audio (R4).

Proves, on synthetic WAVs whose key and tempo are known by construction: the measured
Camelot key and BPM match; a beatless track has an unknown tempo and an atonal one an
unknown key (with reasons), never a number; undecodable bytes leave every feature
unknown; vocal density is always unknown (P6 has no vocal detector).
"""

from __future__ import annotations

import pytest
from media_factory.audio.features import camelot_for, measure_features

from tests.support.media_audio import synth_track


@pytest.mark.parametrize(("bpm", "tonic", "minor", "camelot"), [
    (120.0, 0, False, "8B"),    # C major
    (95.0, 9, True, "8A"),      # A minor
    (140.0, 7, False, "9B"),    # G major
])
def test_key_and_tempo_are_measured(bpm: float, tonic: int, minor: bool, camelot: str) -> None:
    features = measure_features("cand", synth_track(seconds=10, bpm=bpm, tonic=tonic,
                                                    minor=minor))
    assert features.camelot == camelot
    assert features.bpm == pytest.approx(bpm, rel=0.02)
    assert features.energy_mean is not None and 0.0 < features.energy_mean < 1.0
    assert features.vocal_density is None
    assert features.unknown_reasons == {"vocal_density": "no_vocal_detector"}


def test_camelot_wheel_mapping() -> None:
    assert camelot_for(0, False) == "8B"
    assert camelot_for(5, False) == "7B"
    assert camelot_for(11, False) == "1B"
    assert camelot_for(4, True) == "9A"  # E minor, relative of G major


def test_beatless_and_atonal_tracks_have_unknown_features_not_zero() -> None:
    beatless = measure_features("pad", synth_track(seconds=8, clicks=False))
    assert beatless.bpm is None
    assert beatless.unknown_reasons["bpm"] == "no_beat"
    assert beatless.camelot == "8B"

    atonal = measure_features("clicks", synth_track(seconds=8, tonal=False))
    assert atonal.camelot is None
    assert atonal.unknown_reasons["key"] in {"low_correlation", "ambiguous"}
    assert atonal.bpm == pytest.approx(120.0, rel=0.02)


def test_undecodable_audio_leaves_every_feature_unknown() -> None:
    features = measure_features("bad", b"not audio at all")
    assert (features.bpm, features.camelot, features.energy_mean, features.vocal_density) \
        == (None, None, None, None)
    assert set(features.unknown_reasons) == {"bpm", "key", "energy", "vocal_density"}

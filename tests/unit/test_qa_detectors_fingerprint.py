"""The spectral-landmark audio fingerprint (R4, ADR-0034) on synthetic audio.

What these prove: determinism, invariance to gain and start offset, separation of
unrelated synthetic recordings, and that every inconclusive input is UNKNOWN. They do not
measure accuracy on real music.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from quality_gate.detectors import (
    AudioFingerprint,
    AudioUnreadableError,
    DetectorOutcome,
    ReferenceFingerprint,
    fingerprint_file,
    fingerprint_samples,
    load_detector_policy,
    match_fingerprint,
    parse_detector_policy,
)

from tests.support.rights_detectors import SAMPLE_RATE, synthetic_music, write_wav

PARAMS = load_detector_policy().audio_fingerprint


def _match(candidate: AudioFingerprint, entries: list[ReferenceFingerprint], *,
           empty_is_unknown: bool = True) -> object:
    return match_fingerprint(
        candidate, entries, PARAMS, source="reference_set", thresholds_version="t",
        snapshot_ref="s", empty_source_is_unknown=empty_is_unknown,
    )


def _fp(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> AudioFingerprint:
    return fingerprint_samples(samples, sample_rate, PARAMS)


def test_fingerprint_is_deterministic_and_round_trips_through_bytes() -> None:
    """Proves: the same samples give byte-identical fingerprints, and storage round-trips."""
    a = _fp(synthetic_music(3))
    b = _fp(synthetic_music(3))
    assert a.to_bytes() == b.to_bytes()
    assert a.digest() == b.digest()
    back = AudioFingerprint.from_bytes(a.version, a.to_bytes(), a.duration_seconds)
    assert back.digest() == a.digest()


def test_gain_change_does_not_change_the_fingerprint() -> None:
    """Proves: peak normalisation makes the fingerprint gain-invariant (-12 dB here)."""
    music = synthetic_music(4)
    assert _fp(music).digest() == _fp(music * 0.25).digest()


def test_offset_clip_matches_its_source_and_recovers_the_offset() -> None:
    """Proves: a clip starting 2.21 s into a reference matches it at that offset."""
    reference = synthetic_music(5, seconds=10.0)
    start = int(2.21 * SAMPLE_RATE)
    clip = reference[start : start + 5 * SAMPLE_RATE] * 0.6
    result = _match(_fp(clip), [ReferenceFingerprint("ref", _fp(reference))])
    assert result.outcome is DetectorOutcome.MATCH  # type: ignore[attr-defined]
    assert abs(result.metrics["offset_seconds"] - 2.21) < 0.05  # type: ignore[attr-defined]


def test_resampled_copy_still_matches() -> None:
    """Proves: the same recording delivered at 48 kHz matches its 44.1 kHz reference."""
    from scipy.signal import resample_poly

    reference = synthetic_music(6, seconds=8.0)
    at_48k = resample_poly(reference, 160, 147, axis=0)
    result = _match(_fp(at_48k, 48000), [ReferenceFingerprint("ref", _fp(reference))])
    assert result.outcome is DetectorOutcome.MATCH  # type: ignore[attr-defined]


@pytest.mark.parametrize("seed", [30, 31, 32, 33, 34])
def test_unrelated_recordings_do_not_match(seed: int) -> None:
    """Proves: different synthetic recordings stay below the declared thresholds."""
    refs = [ReferenceFingerprint(f"ref_{s}", _fp(synthetic_music(s))) for s in (40, 41, 42)]
    result = _match(_fp(synthetic_music(seed)), refs)
    assert result.outcome is DetectorOutcome.NO_MATCH  # type: ignore[attr-defined]
    assert result.score < PARAMS.match_min_score  # type: ignore[attr-defined]


def test_too_little_signal_is_unknown_not_no_match() -> None:
    """Proves: a candidate with fewer hashes than declared (silence, a 1 s tone) is
    UNKNOWN: an empty fingerprint can never look like 'no match'."""
    refs = [ReferenceFingerprint("ref", _fp(synthetic_music(7)))]
    silence = np.zeros((SAMPLE_RATE * 3, 2))
    tone = np.sin(2 * np.pi * 440 * np.arange(SAMPLE_RATE) / SAMPLE_RATE)
    for samples in (silence, tone):
        result = _match(_fp(samples), refs)
        assert result.outcome is DetectorOutcome.UNKNOWN  # type: ignore[attr-defined]
        assert result.reason == "insufficient_candidate_hashes"  # type: ignore[attr-defined]


def test_empty_source_is_unknown_for_references_and_no_match_for_the_catalog() -> None:
    """Proves: an empty reference set proves nothing (UNKNOWN); an empty internal catalog
    is the legitimate state before anything is released (NO_MATCH)."""
    candidate = _fp(synthetic_music(8))
    assert _match(candidate, []).outcome is DetectorOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert _match(candidate, [], empty_is_unknown=False).outcome is DetectorOutcome.NO_MATCH  # type: ignore[attr-defined]


def test_a_reference_under_another_fingerprint_version_makes_the_source_unknown() -> None:
    """Proves: a stored fingerprint of another version is not silently skipped: the
    source is UNKNOWN until it is re-fingerprinted."""
    current = _fp(synthetic_music(9))
    stale = AudioFingerprint("p5-landmark-v0+000000000000", current.hashes, 6.0)
    result = _match(_fp(synthetic_music(10)), [ReferenceFingerprint("old", stale)])
    assert result.outcome is DetectorOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert result.reason == "fingerprint_version_mismatch"  # type: ignore[attr-defined]


def test_changing_an_extraction_parameter_changes_the_fingerprint_version() -> None:
    """Proves: thresholds are data, and an extraction change cannot reuse the old version."""
    data = load_detector_policy().model_dump()
    data["audio_fingerprint"]["peak_floor_db"] = 35.0
    changed = parse_detector_policy(data).audio_fingerprint
    assert changed.fingerprint_version != PARAMS.fingerprint_version
    data["audio_fingerprint"]["peak_floor_db"] = PARAMS.peak_floor_db
    data["audio_fingerprint"]["match_min_score"] = 0.5  # a decision threshold, not extraction
    assert parse_detector_policy(data).audio_fingerprint.fingerprint_version == (
        PARAMS.fingerprint_version
    )


def test_unreadable_file_raises_audio_unreadable(tmp_path: Path) -> None:
    """Proves: a corrupt or missing file is a typed error the suite turns into UNKNOWN."""
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio")
    with pytest.raises(AudioUnreadableError):
        fingerprint_file(bad, PARAMS)
    with pytest.raises(AudioUnreadableError):
        fingerprint_file(tmp_path / "missing.wav", PARAMS)
    good = write_wav(tmp_path / "good.wav", synthetic_music(11))
    assert fingerprint_file(good, PARAMS).hash_count >= PARAMS.min_candidate_hashes

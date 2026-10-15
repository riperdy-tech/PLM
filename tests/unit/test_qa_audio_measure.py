"""Measurement methods and the declared policy of the P5 audio checks (R4).

These tests call ``quality_gate.audio`` directly on synthesised samples. They prove the
measurement arithmetic and the policy contract, not the evaluators' findings (see
``test_qa_audio_technical.py``) and not FFmpeg decoding (see ``test_qa_audio_media.py``).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from pydantic import ValidationError
from quality_gate.audio.measure import (
    BELOW_ABSOLUTE_GATE,
    CHANNEL_WITHOUT_ENERGY,
    TOO_SHORT_FOR_GATING_BLOCK,
    UNSUPPORTED_CHANNEL_COUNT,
    MeasurementUnknownError,
    clip_stats,
    db_from_linear,
    edge_silence,
    integrated_loudness_lufs,
    sample_peak_linear,
    stereo_correlation,
    true_peak_linear,
)
from quality_gate.audio.policy import load_audio_qa_policy, parse_audio_qa_policy
from scipy.signal import resample_poly  # type: ignore[import-untyped]

from tests.support.audio_fixtures import (
    INTER_SAMPLE_PEAK_AMPLITUDE,
    REFERENCE_LUFS,
    REFERENCE_RATE,
    inter_sample_peak,
    sine,
)

# EBU Tech 3341 requires a loudness meter to read stationary test signals within
# +-0.1 LU; the measured value must meet the same bound.
LOUDNESS_TOLERANCE_LU = 0.1


def test_bs1770_reference_sine_measures_minus_14_lufs() -> None:
    """Proves integrated loudness is BS.1770, not RMS-3 dB: the calibrated 997 Hz
    reference reads -14 LUFS within the EBU meter tolerance."""
    amplitude = 10.0 ** (REFERENCE_LUFS / 20.0)
    samples = sine(997.0, amplitude, 5.0, rate=REFERENCE_RATE)
    measured = integrated_loudness_lufs(samples, REFERENCE_RATE)
    assert abs(measured - REFERENCE_LUFS) <= LOUDNESS_TOLERANCE_LU
    # The old approximation (RMS dBFS - 3) would have read about -20 LUFS here.
    rms_minus_3 = 20 * math.log10(amplitude / math.sqrt(2)) - 3.0
    assert abs(rms_minus_3 - REFERENCE_LUFS) > 5.0


def test_loudness_shorter_than_one_gating_block_is_unknown() -> None:
    """Proves a sub-400 ms file gets no loudness number: it is UNKNOWN with a reason."""
    samples = sine(997.0, 0.2, 0.3)
    with pytest.raises(MeasurementUnknownError) as excinfo:
        integrated_loudness_lufs(samples, 44100)
    assert excinfo.value.reason == TOO_SHORT_FOR_GATING_BLOCK


def test_loudness_of_silence_is_unknown_not_a_number() -> None:
    """Proves a signal entirely under the -70 LUFS absolute gate is UNKNOWN, not -inf."""
    with pytest.raises(MeasurementUnknownError) as excinfo:
        integrated_loudness_lufs(np.zeros((44100, 2)), 44100)
    assert excinfo.value.reason == BELOW_ABSOLUTE_GATE


def test_loudness_beyond_bs1770_channel_weights_is_unknown() -> None:
    """Proves a layout BS.1770 has no weights for is UNKNOWN rather than guessed."""
    with pytest.raises(MeasurementUnknownError) as excinfo:
        integrated_loudness_lufs(sine(997.0, 0.2, 1.0, channels=6), 44100)
    assert excinfo.value.reason == UNSUPPORTED_CHANNEL_COUNT


def test_true_peak_catches_an_inter_sample_peak_the_sample_peak_misses() -> None:
    """Proves 4x oversampling finds the +1.58 dBTP peak between samples of a signal
    whose sample peak is -1.43 dBFS."""
    samples = inter_sample_peak()
    sample_peak_db = db_from_linear(sample_peak_linear(samples))
    true_peak_db = db_from_linear(true_peak_linear(samples, 4))
    assert sample_peak_db == pytest.approx(20 * math.log10(1.2 * math.sqrt(0.5)), abs=1e-6)
    assert sample_peak_db < 0.0 < true_peak_db
    assert true_peak_db == pytest.approx(20 * math.log10(INTER_SAMPLE_PEAK_AMPLITUDE), abs=0.05)


def test_blocked_true_peak_equals_the_whole_signal_computation() -> None:
    """Proves the bounded-memory blocked oversampler is exact: its result equals one
    resample_poly over the whole signal (several blocks plus a ragged tail)."""
    n = np.arange(100_003, dtype=np.float64)
    samples = np.stack(
        [0.5 * np.sin(0.37 * n) + 0.3 * np.sin(2.9 * n), 0.6 * np.cos(1.3 * n + 0.2)], axis=1
    )
    whole = float(np.max(np.abs(resample_poly(samples, 4, 1, axis=0))))
    assert true_peak_linear(samples, 4) == pytest.approx(whole, rel=0, abs=1e-12)


def test_true_peak_is_never_below_the_sample_peak() -> None:
    """Proves the reported true peak cannot under-read a sample that is already there."""
    samples = np.zeros((1000, 1))
    samples[500, 0] = 0.9
    assert true_peak_linear(samples, 4) >= 0.9


def test_clip_events_are_runs_of_consecutive_full_scale_samples() -> None:
    """Proves a single (or two) full-scale sample is not clipping and a run of at least
    the declared length is one event per run per channel."""
    samples = np.zeros((1000, 2))
    samples[10, 0] = 1.0  # a lone peak
    samples[20:22, 0] = 1.0  # two in a row
    samples[100:103, 0] = -1.0  # three in a row: one event
    samples[500:510, 1] = 0.9999  # ten in a row at -0.0009 dBFS: one event
    stats = clip_stats(samples, 1000, level_linear=10 ** (-0.01 / 20), min_run_samples=3)
    assert stats.events == 2
    assert stats.clipped_samples == 13
    assert stats.longest_run_samples == 10
    assert stats.first_event_seconds == pytest.approx(0.1)


def test_edge_silence_is_measured_against_the_threshold() -> None:
    """Proves leading/trailing silence is the sub-threshold run at each end, in seconds,
    and a file with nothing above the threshold is entirely silent."""
    rate = 1000
    samples = np.full((10_000, 2), 1e-4)  # -80 dBFS: below a -60 dBFS threshold
    samples[3000:9000, :] = 0.5
    result = edge_silence(samples, rate, threshold_linear=10 ** (-60 / 20))
    assert result.leading_seconds == pytest.approx(3.0)
    assert result.trailing_seconds == pytest.approx(1.0)
    assert not result.entirely_silent
    assert edge_silence(np.full((100, 2), 1e-4), rate, threshold_linear=1e-3).entirely_silent


def test_stereo_correlation_of_an_inverted_pair_is_minus_one() -> None:
    """Proves the phase correlation reads -1 for L = -R and +1 for L = R."""
    mono = sine(440.0, 0.3, 1.0, channels=1)[:, 0]
    assert stereo_correlation(np.stack([mono, -mono], axis=1)) == pytest.approx(-1.0)
    assert stereo_correlation(np.stack([mono, mono], axis=1)) == pytest.approx(1.0)


def test_stereo_correlation_with_a_dead_channel_is_unknown() -> None:
    """Proves an undefined correlation is UNKNOWN, not 0 (which would read as 'wide')."""
    mono = sine(440.0, 0.3, 1.0, channels=1)[:, 0]
    with pytest.raises(MeasurementUnknownError) as excinfo:
        stereo_correlation(np.stack([mono, np.zeros_like(mono)], axis=1))
    assert excinfo.value.reason == CHANNEL_WITHOUT_ENERGY


def test_packaged_policy_loads_with_a_version() -> None:
    """Proves the shipped thresholds are declared data with a version that fits the
    check's config_version column."""
    policy = load_audio_qa_policy()
    assert policy.policy_version == "p5-audio-qa-v1"
    assert len(policy.policy_version) <= 32
    assert policy.true_peak.oversampling_factor == 4


def _policy_doc(**section_overrides: dict[str, object]) -> dict[str, object]:
    doc: dict[str, object] = load_audio_qa_policy().model_dump(mode="json")
    for section, values in section_overrides.items():
        current = doc[section]
        assert isinstance(current, dict)
        doc[section] = {**current, **values}
    return doc


def test_policy_refuses_incoherent_thresholds() -> None:
    """Proves an invalid policy raises instead of loading with defaults: a block
    threshold looser than the warning, an unknown key, and sub-4x oversampling."""
    for bad in (
        _policy_doc(stereo={"block_below_correlation": 0.5}),
        _policy_doc(clipping={"surprise": 1}),
        _policy_doc(true_peak={"oversampling_factor": 2}),
    ):
        with pytest.raises(ValidationError):
            parse_audio_qa_policy(json.dumps(bad))

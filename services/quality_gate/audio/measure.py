"""Deterministic audio measurements for the P5 technical and musical checks (R4).

Methods (all on float64 PCM in full-scale units, ``(frames, channels)``):

* **Integrated loudness**: ITU-R BS.1770-4 via ``pyloudnorm.Meter`` (K-weighting
  pre-filter and RLB high-pass designed for the file's sample rate, 400 ms blocks with
  75 % overlap, absolute gate at -70 LUFS, relative gate at -10 LU, channel weights
  1.0 for L/R/C and 1.41 for surrounds). It is UNKNOWN when the file is shorter than one
  400 ms gating block, when every block falls below the absolute gate, or when the
  channel count exceeds the five BS.1770 weights.
* **True peak**: BS.1770-4 Annex 2 style. Each channel is upsampled by the policy's
  factor (4x) with ``scipy.signal.resample_poly``: a polyphase FIR from
  ``firwin(2 * 10 * factor + 1, 1 / factor, window=("kaiser", 5.0))`` (81 taps at 4x,
  about 20 per phase, versus the 48-tap reference filter), gain-compensated by the
  factor. The maximum absolute oversampled value is reported in dBTP; it is never below
  the sample peak. Annex 2 bounds the under-read of 4x oversampling at about 0.7 dB for
  content near Nyquist. The signal is processed in fixed blocks with a 64-sample
  context margin on each side (the filter needs 10), so the blocked result equals the
  whole-signal result while memory stays bounded.
* **Sample peak** and **RMS**: over all channels; crest factor = peak dBFS - RMS dBFS.
* **Clipping**: per channel, maximal runs of consecutive samples whose magnitude is at or
  above the policy's full-scale level; a run of at least ``min_run_samples`` is one event.
* **Edge silence**: a frame is silent when its peak over all channels is below the
  threshold; leading/trailing silence is the silent run at each end, in seconds.
* **Stereo correlation**: whole-file phase correlation
  ``sum(L*R) / sqrt(sum(L^2) * sum(R^2))`` for two-channel audio (no DC removal, as a
  phase-correlation meter reads it). UNKNOWN when a channel carries no energy; not
  applicable for other channel counts.

No function reads a clock or a random source, and none depends on hash ordering, so the
same samples always give the same values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pyloudnorm  # type: ignore[import-untyped]
from scipy.signal import resample_poly  # type: ignore[import-untyped]

__all__ = [
    "BELOW_ABSOLUTE_GATE",
    "CHANNEL_WITHOUT_ENERGY",
    "LOUDNESS_GATE_BLOCK_SECONDS",
    "TOO_SHORT_FOR_GATING_BLOCK",
    "UNSUPPORTED_CHANNEL_COUNT",
    "ClipStats",
    "EdgeSilence",
    "MeasurementUnknownError",
    "clip_stats",
    "db_from_linear",
    "edge_silence",
    "integrated_loudness_lufs",
    "linear_from_db",
    "rms_linear",
    "sample_peak_linear",
    "stereo_correlation",
    "true_peak_linear",
]

# BS.1770-4 constants of the method itself (not policy): the gating block length and
# the number of channel weights the standard defines.
LOUDNESS_GATE_BLOCK_SECONDS = 0.4
_BS1770_MAX_CHANNELS = 5

# Reasons a measurement is UNKNOWN.
TOO_SHORT_FOR_GATING_BLOCK = "too_short_for_gating_block"
BELOW_ABSOLUTE_GATE = "below_absolute_gate"
UNSUPPORTED_CHANNEL_COUNT = "unsupported_channel_count"
CHANNEL_WITHOUT_ENERGY = "channel_without_energy"

_TRUE_PEAK_BLOCK_FRAMES = 1 << 15
_TRUE_PEAK_CONTEXT_FRAMES = 64

Samples = npt.NDArray[np.float64]


class MeasurementUnknownError(Exception):
    """A measurement that cannot be made; ``reason`` names why."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def db_from_linear(value: float) -> float:
    """20*log10(value); ``-inf`` for zero (exact silence has no finite level)."""
    return 20.0 * math.log10(value) if value > 0.0 else float("-inf")


def linear_from_db(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def integrated_loudness_lufs(samples: Samples, sample_rate: int) -> float:
    """BS.1770-4 integrated loudness in LUFS, or :class:`MeasurementUnknownError`."""
    frames, channels = samples.shape
    if channels > _BS1770_MAX_CHANNELS:
        raise MeasurementUnknownError(
            UNSUPPORTED_CHANNEL_COUNT,
            f"{channels} channels; BS.1770 defines weights for at most {_BS1770_MAX_CHANNELS}",
        )
    min_frames = math.ceil(LOUDNESS_GATE_BLOCK_SECONDS * sample_rate)
    if frames < min_frames:
        raise MeasurementUnknownError(
            TOO_SHORT_FOR_GATING_BLOCK,
            f"{frames / sample_rate:.3f}s is shorter than one "
            f"{LOUDNESS_GATE_BLOCK_SECONDS}s gating block",
        )
    meter = pyloudnorm.Meter(sample_rate, block_size=LOUDNESS_GATE_BLOCK_SECONDS)
    try:
        value = float(meter.integrated_loudness(samples))
    except ValueError as exc:  # pyloudnorm's own length/shape validation
        raise MeasurementUnknownError(TOO_SHORT_FOR_GATING_BLOCK, str(exc)) from exc
    if not math.isfinite(value):
        raise MeasurementUnknownError(
            BELOW_ABSOLUTE_GATE, "every gating block is below the -70 LUFS absolute gate"
        )
    return value


def sample_peak_linear(samples: Samples) -> float:
    return float(np.max(np.abs(samples))) if samples.size else 0.0


def rms_linear(samples: Samples) -> float:
    if not samples.size:
        return 0.0
    return float(math.sqrt(float(np.mean(np.square(samples)))))


def true_peak_linear(samples: Samples, oversampling_factor: int) -> float:
    """Maximum absolute value of the ``oversampling_factor``-times upsampled signal."""
    frames = samples.shape[0]
    peak = sample_peak_linear(samples)
    for start in range(0, frames, _TRUE_PEAK_BLOCK_FRAMES):
        stop = min(frames, start + _TRUE_PEAK_BLOCK_FRAMES)
        lo = max(0, start - _TRUE_PEAK_CONTEXT_FRAMES)
        hi = min(frames, stop + _TRUE_PEAK_CONTEXT_FRAMES)
        upsampled = resample_poly(samples[lo:hi], oversampling_factor, 1, axis=0)
        first = (start - lo) * oversampling_factor
        last = (stop - lo) * oversampling_factor
        peak = max(peak, float(np.max(np.abs(upsampled[first:last]))))
    return peak


@dataclass(frozen=True)
class ClipStats:
    events: int
    clipped_samples: int
    longest_run_samples: int
    first_event_seconds: float | None


def clip_stats(
    samples: Samples, sample_rate: int, *, level_linear: float, min_run_samples: int
) -> ClipStats:
    """Runs of >= ``min_run_samples`` consecutive samples at or above ``level_linear``."""
    events = 0
    clipped = 0
    longest = 0
    first_frame: int | None = None
    for channel in range(samples.shape[1]):
        hot = np.abs(samples[:, channel]) >= level_linear
        if not hot.any():
            continue
        edges = np.diff(np.concatenate(([0], hot.astype(np.int8), [0])))
        starts = np.flatnonzero(edges == 1)
        lengths = np.flatnonzero(edges == -1) - starts
        qualifying = lengths >= min_run_samples
        if not qualifying.any():
            continue
        events += int(np.count_nonzero(qualifying))
        clipped += int(lengths[qualifying].sum())
        longest = max(longest, int(lengths[qualifying].max()))
        candidate = int(starts[qualifying][0])
        first_frame = candidate if first_frame is None else min(first_frame, candidate)
    return ClipStats(
        events=events,
        clipped_samples=clipped,
        longest_run_samples=longest,
        first_event_seconds=None if first_frame is None else first_frame / sample_rate,
    )


@dataclass(frozen=True)
class EdgeSilence:
    leading_seconds: float
    trailing_seconds: float
    entirely_silent: bool


def edge_silence(samples: Samples, sample_rate: int, *, threshold_linear: float) -> EdgeSilence:
    frames = samples.shape[0]
    audible = np.flatnonzero(np.max(np.abs(samples), axis=1) >= threshold_linear)
    if audible.size == 0:
        whole = frames / sample_rate
        return EdgeSilence(leading_seconds=whole, trailing_seconds=whole, entirely_silent=True)
    return EdgeSilence(
        leading_seconds=int(audible[0]) / sample_rate,
        trailing_seconds=(frames - 1 - int(audible[-1])) / sample_rate,
        entirely_silent=False,
    )


def stereo_correlation(samples: Samples) -> float:
    """Whole-file L/R phase correlation in [-1, 1]; requires exactly two channels."""
    if samples.shape[1] != 2:
        raise ValueError("stereo correlation needs exactly two channels")
    left = samples[:, 0]
    right = samples[:, 1]
    energy = float(np.dot(left, left)) * float(np.dot(right, right))
    if energy <= 0.0:
        raise MeasurementUnknownError(
            CHANNEL_WITHOUT_ENERGY, "a channel carries no energy, so correlation is undefined"
        )
    value = float(np.dot(left, right)) / math.sqrt(energy)
    return max(-1.0, min(1.0, value))

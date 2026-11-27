"""Signal estimates for the channel-identity checks: pulse strength, sung vocals and a
stuck loop (P5, review 2026-11-26 #50 and #51).

All three work on mono float PCM, read no clock or random source, and are estimates,
not a listening review; the identity evaluator turns them into findings with the
thresholds of ``identity_profiles.json``.

* :func:`pulse_strengths`: the onset-envelope autocorrelation P6's tempo estimator uses
  (:mod:`media_factory.audio.features`: spectral flux of a log spectrogram at about
  11 kHz, hop 128, Gaussian smoothing), read at the lag of each asked tempo (best of the
  lag and its neighbours). A periodic beat at B BPM is about as strong at B/2 (every
  other beat) as at B; a track felt half-time is strong at both.
* :func:`vocal_presence`: the share of 46 ms frames that carry a clear pitch between 100
  and 800 Hz (normalised autocorrelation of the 1.5 kHz low band) whose pitch, over the
  half second around it, moves the way a voice does: by more than a held note and less
  than a melody's leap, and continuously, without the step of a played note (vibrato,
  glides; no 20 ms step of 50 cents or more). A held pad, a click or noise has no such
  frame; a synth lead with vibrato can, which is why the result is a hold for a
  listener, never a rejection.
* :func:`loop_repetition`: the strongest waveform repetition period between 1 and 60 s
  (autocorrelation of the 4 kHz signal) and the share of the track whose 5 s windows
  match the window one period later with correlation at least 0.98. Played or
  generated music that repeats a phrase varies its layers, phase and dynamics; a track
  that is one clip copied end to end matches almost sample for sample. The same share
  at the strongest period under 1 s tells a steady drone or bare beat (which matches
  there too) from a varied clip that repeats.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

__all__ = ["LoopRepetition", "loop_repetition", "pulse_strengths", "vocal_presence"]

FloatArray = npt.NDArray[np.float64]

_ANALYSIS_RATE = 11025
_HOP = 128


def _downsample(mono: FloatArray, rate: int, target: int) -> tuple[FloatArray, float]:
    factor = max(1, round(rate / target))
    usable = len(mono) - len(mono) % factor
    return (np.ascontiguousarray(mono[:usable].reshape(-1, factor).mean(axis=1)),
            rate / factor)


def _frames(signal: FloatArray, size: int, hop: int) -> FloatArray:
    if len(signal) < size:
        signal = np.pad(signal, (0, size - len(signal)))
    count = 1 + (len(signal) - size) // hop
    framed: FloatArray = np.lib.stride_tricks.as_strided(
        signal, shape=(count, size), strides=(signal.strides[0] * hop, signal.strides[0]),
        writeable=False)
    return framed


def pulse_strengths(mono: FloatArray, rate: int,
                    bpms: Sequence[float]) -> list[float | None]:
    """Onset-envelope autocorrelation (normalised, ``>= 0``) at each tempo in ``bpms``;
    ``None`` for a tempo whose lag the track cannot hold or a track with no onsets."""
    analysis, analysis_rate = _downsample(mono, rate, _ANALYSIS_RATE)
    window = np.hanning(1024)
    framed = _frames(analysis, 1024, _HOP)
    spectrum = np.concatenate([
        np.log1p(100.0 * np.abs(np.fft.rfft(framed[i:i + 2048] * window, axis=1)))
        for i in range(0, len(framed), 2048)])
    flux = np.maximum(0.0, np.diff(spectrum, axis=0)).sum(axis=1)
    if len(flux) < 8 or not np.any(flux):
        return [None for _ in bpms]
    kernel = np.exp(-0.5 * (np.arange(-4, 5) / 1.5) ** 2)
    envelope = np.convolve(flux, kernel / kernel.sum(), mode="same")
    envelope = envelope - envelope.mean()
    n = len(envelope)
    spec = np.fft.rfft(envelope, 2 * n)
    autocorr = np.fft.irfft(spec * np.conj(spec))[:n]
    if autocorr[0] <= 0:
        return [None for _ in bpms]
    autocorr = autocorr / autocorr[0]
    frame_rate = analysis_rate / _HOP
    found: list[float | None] = []
    for bpm in bpms:
        lag = round(60.0 * frame_rate / bpm)
        if lag < 2 or lag + 1 >= n // 2:
            found.append(None)
            continue
        found.append(max(0.0, float(autocorr[lag - 1:lag + 2].max())))
    return found


def vocal_presence(mono: FloatArray, rate: int) -> float | None:
    """Share of frames with a voice-like moving pitch (0..1); ``None`` when too short."""
    low, low_rate = _downsample(mono, rate, 8000)
    # A gentle low-pass: the pitch of a voice lives below 1.5 kHz.
    spectrum = np.fft.rfft(low)
    spectrum[np.fft.rfftfreq(len(low), 1.0 / low_rate) > 1500.0] = 0.0
    low = np.fft.irfft(spectrum, len(low))
    size = int(0.046 * low_rate)
    hop = int(0.02 * low_rate)
    if len(low) < size * 30:
        return None
    frames = _frames(low, size, hop) * np.hanning(size)
    min_lag, max_lag = int(low_rate / 800.0), int(low_rate / 100.0)
    if max_lag + 2 >= size:
        return None
    spec = np.fft.rfft(frames, 2 * size, axis=1)
    autocorr = np.fft.irfft(spec * np.conj(spec), axis=1)[:, :size]
    energy = autocorr[:, 0]
    loud = energy > max(float(np.percentile(energy, 90)) * 1e-3, 1e-12)
    norm = autocorr[:, min_lag:max_lag + 1] / np.maximum(energy[:, None], 1e-12)
    best = norm.argmax(axis=1)
    rows = np.arange(len(norm))
    clarity = norm[rows, best]
    voiced = loud & (clarity >= 0.7)
    # Parabolic refinement: whole-sample lags alone are tens of cents apart.
    inner = np.clip(best, 1, norm.shape[1] - 2)
    a, b, c = norm[rows, inner - 1], norm[rows, inner], norm[rows, inner + 1]
    denominator = a - 2 * b + c
    offset = np.where(np.abs(denominator) > 1e-12,
                      0.5 * (a - c) / np.where(denominator == 0, 1.0, denominator), 0.0)
    lags = min_lag + inner + np.clip(offset, -0.5, 0.5)
    cents = 1200.0 * np.log2(low_rate / lags)
    span = max(1, int(0.25 / 0.02))
    moving = np.zeros(len(cents), dtype=bool)
    for i in np.flatnonzero(voiced):
        lo, hi = max(0, i - span), min(len(cents), i + span + 1)
        if not voiced[lo:hi].all():
            continue
        around = cents[lo:hi]
        # Moving (vibrato, a glide), not held, and continuous: a played melody steps
        # from note to note, a voice slides.
        spread = float(np.std(around))
        moving[i] = 15.0 <= spread <= 120.0 and float(np.abs(np.diff(around)).max()) < 50.0
    return float(moving.mean())


@dataclass(frozen=True)
class LoopRepetition:
    period_seconds: float
    repeated_share: float
    """Share of the track's 5 s windows that repeat one period later (corr >= 0.98)."""
    steady_share: float
    """The same share at the strongest period under 1 s: a drone or a bare beat repeats
    within a second, which is steadiness, not a clip copied end to end."""


def _strongest_lag(autocorr: FloatArray, low: int, high: int) -> int:
    overlap = len(autocorr) - np.arange(low, high + 1)
    return low + int(np.argmax(autocorr[low:high + 1] / overlap))


def _match_share(signal: FloatArray, lag: int, window: int) -> float | None:
    matches = []
    for start in range(0, len(signal) - lag - window + 1, window):
        a = signal[start:start + window]
        b = signal[start + lag:start + lag + window]
        denominator = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
        matches.append(denominator > 0 and float(np.dot(a, b)) / denominator >= 0.98)
    return float(np.mean(matches)) if matches else None


def loop_repetition(mono: FloatArray, rate: int) -> LoopRepetition | None:
    """The strongest waveform repetition of the track; ``None`` when it is too short."""
    signal, signal_rate = _downsample(mono, rate, 4000)
    signal = signal - signal.mean()
    n = len(signal)
    min_lag, max_lag = int(1.0 * signal_rate), int(min(60.0 * signal_rate, n / 2))
    window = int(5.0 * signal_rate)
    if max_lag <= min_lag or n < 2 * window or not np.any(signal):
        return None
    spec = np.fft.rfft(signal, 2 * n)
    autocorr = np.fft.irfft(spec * np.conj(spec))[:n]
    lag = _strongest_lag(autocorr, min_lag, max_lag)
    short = _strongest_lag(autocorr, max(1, int(0.02 * signal_rate)), min_lag - 1)
    repeated, steady = _match_share(signal, lag, window), _match_share(signal, short, window)
    if repeated is None or steady is None:
        return None
    return LoopRepetition(period_seconds=lag / signal_rate, repeated_share=repeated,
                          steady_share=steady)

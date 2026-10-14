"""Synthetic audio for P6 tests: tonal, rhythmic WAV tracks with known key and tempo.

A track is a sustained triad of its key's tonic chord plus softer scale tones, with a
decaying percussive click on every beat. Its key and tempo are therefore known by
construction, which is what the feature and sequencing tests check against.
"""

from __future__ import annotations

import io

import numpy as np
import soundfile as sf

__all__ = ["synth_track"]

_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)


def synth_track(
    *,
    seconds: float,
    bpm: float = 120.0,
    tonic: int = 0,
    minor: bool = False,
    amplitude: float = 0.25,
    rate: int = 44100,
    clicks: bool = True,
    tonal: bool = True,
) -> bytes:
    """A stereo 16-bit WAV of ``seconds`` with the given key (pitch class) and tempo."""
    t = np.arange(int(seconds * rate)) / rate
    signal = np.zeros_like(t)
    if tonal:
        scale = _MINOR_SCALE if minor else _MAJOR_SCALE
        chord = (scale[0], scale[2], scale[4])
        for degree in scale:
            midi = 60 + tonic + degree
            freq = 440.0 * 2 ** ((midi - 69) / 12)
            weight = 1.0 if degree in chord else 0.35
            signal += weight * np.sin(2 * np.pi * freq * t)
            signal += 0.5 * weight * np.sin(2 * np.pi * freq / 2 * t)
        signal /= np.max(np.abs(signal)) or 1.0
        signal *= 0.6
    if clicks:
        beat = 60.0 / bpm
        decay = np.exp(-np.arange(int(0.08 * rate)) / (0.012 * rate))
        burst = decay * np.sin(2 * np.pi * 90.0 * np.arange(len(decay)) / rate)
        for start in np.arange(0.0, seconds, beat):
            i = int(start * rate)
            end = min(len(signal), i + len(burst))
            signal[i:end] += burst[: end - i]
    peak = np.max(np.abs(signal)) or 1.0
    stereo = np.stack([signal, signal], axis=1) * (amplitude / peak)
    buffer = io.BytesIO()
    sf.write(buffer, stereo, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()

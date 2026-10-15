"""Golden audio fixtures for the P5 audio checks, synthesised deterministically (R4).

Nothing binary is committed: every fixture is generated from a closed-form signal
(no random source, no clock) and written with libsndfile, so the same call always gives
the same bytes. The known values each fixture is built to have are stated beside it.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf  # type: ignore[import-untyped]

__all__ = [
    "CLEAN_LUFS",
    "INTER_SAMPLE_PEAK_AMPLITUDE",
    "RATE",
    "REFERENCE_LUFS",
    "REFERENCE_RATE",
    "clean_sine",
    "fade",
    "inter_sample_peak",
    "reference_minus_14_lufs",
    "sine",
    "write_wav",
]

RATE = 44100  # the policy's expected delivery rate
REFERENCE_RATE = 48000  # the EBU Tech 3341 test-signal rate
# BS.1770 calibration: a 997 Hz sine of peak amplitude A in both channels of a stereo
# signal reads 20*log10(A) LUFS (the -0.691 dB offset cancels the K-weighting gain at
# 997 Hz). So A = 10**(-14/20) is a -14 LUFS reference.
REFERENCE_LUFS = -14.0
CLEAN_LUFS = -14.0
# A sine at fs/4 sampled at 45 degrees phase hits only +-A*sin(45deg) = +-0.707*A:
# with A = 1.2 the sample peak is -1.43 dBFS, but the reconstructed waveform peaks at
# 1.2 = +1.58 dBTP between samples.
INTER_SAMPLE_PEAK_AMPLITUDE = 1.2

Samples = npt.NDArray[np.float64]


def sine(
    frequency: float,
    amplitude: float,
    seconds: float,
    *,
    rate: int = RATE,
    channels: int = 2,
    phase: float = 0.0,
) -> Samples:
    n = np.arange(round(seconds * rate), dtype=np.float64)
    mono = amplitude * np.sin(2.0 * math.pi * frequency * n / rate + phase)
    return np.repeat(mono[:, None], channels, axis=1)


def fade(samples: Samples, seconds: float, rate: int) -> Samples:
    """Raised-cosine fade in and out, so the signal starts and ends band-limited."""
    length = round(seconds * rate)
    ramp = np.sin(np.linspace(0.0, math.pi / 2.0, length)) ** 2
    window = np.ones(samples.shape[0])
    window[:length] = ramp
    window[-length:] = ramp[::-1]
    out: Samples = samples * window[:, None]
    return out


def write_wav(path: Path, samples: Samples, *, rate: int = RATE, subtype: str = "PCM_16") -> Path:
    sf.write(str(path), samples, rate, subtype=subtype, format="WAV")
    return path


def reference_minus_14_lufs(path: Path, seconds: float = 5.0) -> Path:
    """997 Hz stereo sine at 48 kHz whose BS.1770 loudness is exactly -14 LUFS."""
    amplitude = 10.0 ** (REFERENCE_LUFS / 20.0)
    return write_wav(
        path, sine(997.0, amplitude, seconds, rate=REFERENCE_RATE), rate=REFERENCE_RATE
    )


def clean_sine(seconds: float = 2.0) -> Samples:
    """A 997 Hz stereo sine at 44.1 kHz and -14 LUFS: no defect of any kind."""
    return sine(997.0, 10.0 ** (CLEAN_LUFS / 20.0), seconds)


def inter_sample_peak(seconds: float = 2.0) -> Samples:
    """fs/4 sine at 45 degrees, faded: sample peak -1.43 dBFS, true peak +1.58 dBTP."""
    raw = sine(RATE / 4.0, INTER_SAMPLE_PEAK_AMPLITUDE, seconds, phase=math.pi / 4.0)
    return fade(raw, 0.01, RATE)

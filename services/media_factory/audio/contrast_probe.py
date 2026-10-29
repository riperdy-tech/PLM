"""Read-only offline album-contrast diagnostics from decoded local audio.

These are coarse *proxies*, not transcription, melody recognition, legal
similarity, calibrated thresholds, release clearance or P5 rights decisions.
Unknown measurements remain null. This module makes no database, provider or
artifact-store calls and is not used by production approval gates.
"""
from __future__ import annotations

import hashlib
import io
import math
from collections.abc import Mapping
from dataclasses import dataclass
from math import gcd
from typing import Final

import numpy as np
import numpy.typing as npt
import soundfile as sf  # type: ignore[import-untyped]
from scipy.signal import resample_poly  # type: ignore[import-untyped]

FloatArray = npt.NDArray[np.float64]
POLICY_VERSION: Final[str] = "p6-offline-contrast-v3"
_ANALYSIS_RATE: Final[int] = 11025
_MAX_SECONDS: Final[int] = 900
_METRICS: Final[tuple[str, ...]] = (
    "rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy",
)


@dataclass(frozen=True, slots=True)
class ContrastFeatures:
    sha256: str
    vectors: dict[str, tuple[float, ...] | None]
    unknown: dict[str, str]


def _summary(values: FloatArray, bins: int) -> tuple[float, ...] | None:
    """Resample a nonnegative time series by temporal averaging, then unit-normalise."""
    if not len(values) or not np.all(np.isfinite(values)):
        return None
    edges = np.linspace(0, len(values), bins + 1, dtype=int)
    out = np.array([
        float(values[min(edges[i], len(values) - 1):min(
            len(values), max(edges[i] + 1, edges[i + 1])
        )].mean())
        for i in range(bins)
    ])
    magnitude = float(np.linalg.norm(out))
    if not math.isfinite(magnitude) or magnitude <= 1e-10:
        return None
    return tuple((out / magnitude).tolist())


def measure_contrast(audio: bytes) -> ContrastFeatures:
    """Analyse WAV/FLAC/other libsndfile-readable bytes without mutating anything."""
    digest = hashlib.sha256(audio).hexdigest()
    unknown: dict[str, str] = {}
    empty: dict[str, tuple[float, ...] | None] = dict.fromkeys(_METRICS)
    try:
        with sf.SoundFile(io.BytesIO(audio)) as reader:
            rate = reader.samplerate
            if (rate < 8000 or rate > 96000 or reader.channels < 1
                    or reader.channels > 8 or reader.frames / rate > _MAX_SECONDS):
                raise ValueError("unsupported_rate_channels_or_length")
            if reader.frames < rate:
                raise ValueError("short_or_nonfinite")
            scale = gcd(rate, _ANALYSIS_RATE)
            pieces: list[FloatArray] = []
            # Reading at most ten seconds of multichannel PCM bounds decoded
            # memory independently of the provider's container/channel layout.
            while True:
                block = reader.read(frames=rate * 10, dtype="float64", always_2d=True)
                if not len(block):
                    break
                if not np.all(np.isfinite(block)):
                    raise ValueError("short_or_nonfinite")
                mono = block.mean(axis=1)
                pieces.append(np.asarray(resample_poly(
                    mono, _ANALYSIS_RATE // scale, rate // scale,
                ), dtype=np.float64))
        if not pieces:
            raise ValueError("short_or_nonfinite")
        signal: FloatArray = np.concatenate(pieces)
    except (OSError, ValueError, RuntimeError, sf.LibsndfileError) as exc:
        reason = "unsupported_audio" if not isinstance(exc, ValueError) else str(exc)
        return ContrastFeatures(digest, empty, dict.fromkeys(_METRICS, reason))

    # The decoded frames may be finite while resampling still produces an
    # invalid number. Never let NaN/Inf reach a spectral cosine comparison.
    if len(signal) < _ANALYSIS_RATE:
        return ContrastFeatures(digest, empty, dict.fromkeys(_METRICS, "short_or_nonfinite"))
    if not np.all(np.isfinite(signal)):
        return ContrastFeatures(digest, empty, dict.fromkeys(_METRICS, "nonfinite_resampling"))
    if float(np.max(np.abs(signal))) > 16.0:
        return ContrastFeatures(digest, empty, dict.fromkeys(_METRICS, "out_of_range_pcm"))
    if float(np.sqrt(np.mean(signal * signal))) < 1e-5:
        return ContrastFeatures(digest, empty, dict.fromkeys(_METRICS, "silent_or_short"))

    hop, size = 1024, 4096
    frames = np.lib.stride_tricks.sliding_window_view(signal, size)[::hop]
    # Compute Fourier frames in bounded batches. A full-track spectrum for
    # 15-minute audio can otherwise allocate hundreds of MB.
    frequencies = np.fft.rfftfreq(size, d=1 / _ANALYSIS_RATE)
    usable = (frequencies >= 80.0) & (frequencies <= 2100.0)
    midi = np.rint(69 + 12 * np.log2(frequencies[usable] / 440.0)).astype(int)
    pitch_classes = midi % 12
    chroma_sum = np.zeros((8, 12), dtype=np.float64)
    chroma_count = np.zeros(8, dtype=np.int64)
    flux_parts: list[FloatArray] = []
    previous: FloatArray | None = None
    window = np.hanning(size)
    for start in range(0, len(frames), 128):
        spectrum = np.abs(np.fft.rfft(frames[start:start + 128] * window, axis=1))
        log_spectrum = np.log1p(spectrum)
        if previous is not None:
            flux_parts.append(np.array([
                np.maximum(log_spectrum[0] - previous, 0.0).sum(),
            ]))
        if len(log_spectrum) > 1:
            flux_parts.append(np.maximum(
                np.diff(log_spectrum, axis=0), 0.0,
            ).sum(axis=1))
        previous = log_spectrum[-1].copy()
        band = spectrum[:, usable]
        chroma = np.stack([
            band[:, pitch_classes == note].sum(axis=1) for note in range(12)
        ], axis=1)
        section = np.minimum(
            7, (np.arange(start, start + len(chroma)) * 8) // len(frames),
        )
        for index in range(8):
            chosen = section == index
            if np.any(chosen):
                chroma_sum[index] += chroma[chosen].sum(axis=0)
                chroma_count[index] += int(chosen.sum())

    flux = np.concatenate(flux_parts) if flux_parts else np.array([])
    rhythm = _summary(flux, 32) if len(flux) and float(np.std(flux)) > 1e-5 else None
    if rhythm is None:
        unknown["rhythm_onset_proxy"] = "no_measurable_onset_variation"

    pitch_motion: tuple[float, ...] | None = None
    if np.all(chroma_count > 0):
        chroma_blocks = chroma_sum / chroma_count[:, None]
        norms = np.linalg.norm(chroma_blocks, axis=1)
        if np.all(norms > 1e-8):
            chroma_blocks /= norms[:, None]
            pitch_motion = tuple(
                (chroma_blocks.ravel() / math.sqrt(len(chroma_blocks))).tolist()
            )
    if pitch_motion is None:
        unknown["pitch_class_motion_proxy"] = "insufficient_tonal_content_or_sections"

    power = signal * signal
    window_samples = max(1, _ANALYSIS_RATE // 5)
    frame_count = len(power) // window_samples
    rms = np.sqrt(power[:frame_count * window_samples]
                  .reshape(-1, window_samples).mean(axis=1))
    # Compress level so gain changes do not invent an energy arc.
    energy = _summary(rms, 16)
    if energy is None:
        unknown["energy_arc_proxy"] = "insufficient_energy_evolution"

    return ContrastFeatures(digest, {
        "rhythm_onset_proxy": rhythm,
        "pitch_class_motion_proxy": pitch_motion,
        "energy_arc_proxy": energy,
    }, unknown)


_VECTOR_LENGTHS: Final[dict[str, int]] = {
    "rhythm_onset_proxy": 32,
    "pitch_class_motion_proxy": 96,
    "energy_arc_proxy": 16,
}


def _valid_features(entry: ContrastFeatures) -> ContrastFeatures:
    """Fail unknown rather than comparing corrupt or out-of-contract vectors."""
    vectors: dict[str, tuple[float, ...] | None] = {}
    unknown = dict(entry.unknown)
    for metric, expected_length in _VECTOR_LENGTHS.items():
        vector = entry.vectors.get(metric)
        if vector is None:
            vectors[metric] = None
            unknown.setdefault(metric, "not_measured")
            continue
        if (
            not isinstance(vector, tuple)
            or len(vector) != expected_length
            or any(
                not isinstance(value, (float, int))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
                for value in vector
            )
            or not 0.999 <= math.hypot(*vector) <= 1.001
        ):
            vectors[metric] = None
            unknown[metric] = "invalid_feature_vector"
        else:
            vectors[metric] = vector
    return ContrastFeatures(entry.sha256, vectors, unknown)


def _cosine(left: tuple[float, ...] | None,
            right: tuple[float, ...] | None) -> float | None:
    if left is None or right is None:
        return None
    score = math.fsum(a * b for a, b in zip(left, right, strict=True))
    return round(max(0.0, min(1.0, score)), 6)


def probe_album_features(measured: Mapping[str, ContrastFeatures]) -> dict[str, object]:
    """Compare already measured tracks without keeping their audio bytes resident."""
    if len(measured) < 2 or len(measured) > 20 or any(not name.strip() for name in measured):
        raise ValueError("provide 2..20 distinctly named tracks")
    verified = {name: _valid_features(value) for name, value in measured.items()}
    measured = verified
    ids = sorted(measured)
    pairs = [
        {
            "left": left, "right": right,
            "metrics": {
                metric: _cosine(measured[left].vectors[metric], measured[right].vectors[metric])
                for metric in _METRICS
            },
        }
        for i, left in enumerate(ids) for right in ids[i + 1:]
    ]
    return {
        "policy_version": POLICY_VERSION,
        "evaluation": "diagnostic_only_no_threshold_no_clearance",
        "coverage": {
            name: {"sha256": measured[name].sha256,
                   "unknown": measured[name].unknown}
            for name in ids
        },
        "pairs": pairs,
        "limitations": [
            "onset-pattern proxy is not rhythm transcription",
            "pitch-class distribution over sections is not melody recognition",
            "energy trajectory is not song structure understanding",
            "similarity is uncalibrated and cannot establish originality or rights",
        ],
    }


def probe_album_contrast(tracks: Mapping[str, bytes]) -> dict[str, object]:
    """Convenience API for in-memory fixtures; CLI measures one file at a time."""
    if len(tracks) < 2 or len(tracks) > 20 or any(not name.strip() for name in tracks):
        raise ValueError("provide 2..20 distinctly named tracks")
    return probe_album_features({
        name: measure_contrast(audio) for name, audio in tracks.items()
    })

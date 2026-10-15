"""Sequencing features measured from the cleared track audio (P6 §9.1, R4).

The C5 manifest carries no musical features, and the P3/P4 targets (BPM, key profile)
are what was *asked for*, not what a provider delivered. So P6 measures the features it
sequences on from the verified bytes it is about to render:

* **energy** (``energy_in``, ``energy_out``, ``energy_mean``): mean power of the first
  and last :attr:`FeatureExtractionPolicy.edge_fraction` of the track and of the whole
  track, in dBFS, mapped linearly onto 0..1 between ``energy_floor_db`` and
  ``energy_ceiling_db``. A (near-)silent track has no measurable energy.
* **BPM**: autocorrelation of a smoothed spectral-flux onset envelope over the
  ``bpm_min``..``bpm_max`` range, weighted by a log-normal tempo prior (to settle the
  octave ambiguity) and refined by a parabolic fit. A track whose onset envelope has too
  little contrast (sustained, beatless audio) has no tempo. The confidence is the
  normalised autocorrelation at the chosen lag; below ``bpm_min_confidence`` the tempo
  is unknown.
* **key** (Camelot notation): a chromagram correlated against the Krumhansl-Kessler
  major and minor profiles for all 24 keys. The key is unknown when the best
  correlation is below ``key_min_correlation`` or does not beat the best non-relative
  alternative by ``key_min_margin``.
* **vocal density**: P6 has no vocal detector, so it is always unknown here
  (``no_vocal_detector``). The sequencer scores unknown as worst case (see
  :mod:`media_factory.playlist.sequencer`).

Every unknown carries its reason in :attr:`TrackFeatures.unknown_reasons`; an unknown
is ``None``, never ``0``. These are signal-processing estimates, not ground truth: they
are good enough to order a playlist and are labelled with the extractor version.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import soundfile as sf  # type: ignore[import-untyped]

__all__ = [
    "DEFAULT_FEATURE_POLICY",
    "FeatureExtractionPolicy",
    "TrackFeatures",
    "camelot_for",
    "measure_features",
]

FloatArray = npt.NDArray[np.float64]

_MAJOR_PROFILE: Final = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE: Final = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_ANALYSIS_RATE: Final[int] = 11025


@dataclass(frozen=True, slots=True)
class FeatureExtractionPolicy:
    """Declared thresholds of the feature extractor (versioned data)."""

    version: str = "p6-features-v1"
    edge_fraction: float = 0.15
    energy_floor_db: float = -50.0
    energy_ceiling_db: float = -6.0
    silence_db: float = -70.0
    bpm_min: float = 60.0
    bpm_max: float = 180.0
    bpm_min_confidence: float = 0.2
    onset_min_contrast: float = 1.0
    """Std/mean of the onset envelope below which a track has no beat to measure."""
    bpm_prior_center: float = 120.0
    bpm_prior_octaves: float = 1.0
    key_min_correlation: float = 0.5
    key_min_margin: float = 0.03

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


DEFAULT_FEATURE_POLICY: Final = FeatureExtractionPolicy()


@dataclass(frozen=True, slots=True)
class TrackFeatures:
    """One track's sequencing features; ``None`` means unknown (reason recorded)."""

    candidate_id: str
    bpm: float | None
    bpm_confidence: float | None
    camelot: str | None
    key_confidence: float | None
    energy_in: float | None
    energy_out: float | None
    energy_mean: float | None
    vocal_density: float | None
    extractor_version: str
    unknown_reasons: dict[str, str] = field(default_factory=dict)

    def to_report(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "bpm": None if self.bpm is None else round(self.bpm, 2),
            "bpm_confidence": _r(self.bpm_confidence),
            "camelot": self.camelot,
            "key_confidence": _r(self.key_confidence),
            "energy_in": _r(self.energy_in),
            "energy_out": _r(self.energy_out),
            "energy_mean": _r(self.energy_mean),
            "vocal_density": _r(self.vocal_density),
            "extractor_version": self.extractor_version,
            "unknown": dict(sorted(self.unknown_reasons.items())),
        }


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def camelot_for(tonic_pitch_class: int, minor: bool) -> str:
    """Camelot code of a key: C major is ``8B``, A minor is ``8A``."""
    major_tonic = (tonic_pitch_class + 3) % 12 if minor else tonic_pitch_class
    number = ((major_tonic * 7) % 12 + 7) % 12 + 1
    return f"{number}{'A' if minor else 'B'}"


def _frames(signal: FloatArray, size: int, hop: int) -> FloatArray:
    if len(signal) < size:
        signal = np.pad(signal, (0, size - len(signal)))
    count = 1 + (len(signal) - size) // hop
    strides = (signal.strides[0] * hop, signal.strides[0])
    framed: FloatArray = np.lib.stride_tricks.as_strided(
        signal, shape=(count, size), strides=strides, writeable=False)
    return framed


def _spectrogram(signal: FloatArray, size: int, hop: int) -> FloatArray:
    window = np.hanning(size)
    framed = _frames(signal, size, hop)
    chunks = [np.abs(np.fft.rfft(framed[i:i + 2048] * window, axis=1))
              for i in range(0, len(framed), 2048)]
    return np.concatenate(chunks, axis=0)


def _energy(mono: FloatArray, rate: float, policy: FeatureExtractionPolicy,
            unknown: dict[str, str]) -> tuple[float | None, float | None, float | None]:
    block = max(1, int(rate * 0.1))
    usable = len(mono) - len(mono) % block
    if usable == 0:
        unknown["energy"] = "too_short"
        return None, None, None
    power = (mono[:usable].reshape(-1, block) ** 2).mean(axis=1)
    edge = max(1, round(len(power) * policy.edge_fraction))

    def level(values: FloatArray) -> float:
        return float(10.0 * np.log10(max(float(values.mean()), 1e-20)))

    whole = level(power)
    if whole < policy.silence_db:
        unknown["energy"] = "silent"
        return None, None, None
    span = policy.energy_ceiling_db - policy.energy_floor_db

    def norm(db: float) -> float:
        return float(min(1.0, max(0.0, (db - policy.energy_floor_db) / span)))

    return norm(level(power[:edge])), norm(level(power[-edge:])), norm(whole)


def _tempo(mono: FloatArray, rate: float, policy: FeatureExtractionPolicy,
           unknown: dict[str, str]) -> tuple[float | None, float | None]:
    hop = 128
    spectrum = np.log1p(100.0 * _spectrogram(mono, 1024, hop))
    flux = np.maximum(0.0, np.diff(spectrum, axis=0)).sum(axis=1)
    if len(flux) < 8 or not np.any(flux):
        unknown["bpm"] = "no_onsets"
        return None, None
    if float(flux.std() / flux.mean()) < policy.onset_min_contrast:
        unknown["bpm"] = "no_beat"
        return None, None
    kernel = np.exp(-0.5 * (np.arange(-4, 5) / 1.5) ** 2)
    smoothed = np.convolve(flux, kernel / kernel.sum(), mode="same")
    envelope = smoothed - smoothed.mean()
    n = len(envelope)
    spectrum_ac = np.fft.rfft(envelope, 2 * n)
    autocorr = np.fft.irfft(spectrum_ac * np.conj(spectrum_ac))[:n]
    if autocorr[0] <= 0:
        unknown["bpm"] = "no_onsets"
        return None, None
    autocorr = autocorr / autocorr[0]
    frame_rate = rate / hop
    lo = int(np.floor(60.0 * frame_rate / policy.bpm_max))
    hi = int(np.ceil(60.0 * frame_rate / policy.bpm_min))
    if hi + 1 >= n or lo < 1:
        unknown["bpm"] = "too_short"
        return None, None
    lags = np.arange(lo, hi + 1)
    octaves = np.log2((60.0 * frame_rate / lags) / policy.bpm_prior_center)
    prior = np.exp(-0.5 * (octaves / policy.bpm_prior_octaves) ** 2)
    lag = lo + int(np.argmax(np.maximum(autocorr[lo:hi + 1], 0.0) * prior))
    confidence = float(autocorr[lag])
    if confidence < policy.bpm_min_confidence:
        unknown["bpm"] = "low_confidence"
        return None, confidence
    a, b, c = autocorr[lag - 1], autocorr[lag], autocorr[lag + 1]
    denominator = a - 2 * b + c
    offset = 0.5 * (a - c) / denominator if denominator != 0 else 0.0
    refined = lag + float(max(-0.5, min(0.5, offset)))
    return 60.0 * frame_rate / refined, confidence


def _key(mono: FloatArray, rate: float, policy: FeatureExtractionPolicy,
         unknown: dict[str, str]) -> tuple[str | None, float | None]:
    size = 4096
    magnitude = _spectrogram(mono, size, size // 2).sum(axis=0)
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    band = (freqs >= 65.0) & (freqs <= 2100.0)
    if not np.any(magnitude[band]):
        unknown["key"] = "no_tonal_content"
        return None, None
    pitch = np.round(12.0 * np.log2(freqs[band] / 440.0) + 69.0).astype(int) % 12
    chroma = np.bincount(pitch, weights=magnitude[band], minlength=12)
    scores: dict[tuple[int, bool], float] = {}
    for tonic in range(12):
        for minor, profile in ((False, _MAJOR_PROFILE), (True, _MINOR_PROFILE)):
            scores[(tonic, minor)] = float(np.corrcoef(chroma, np.roll(profile, tonic))[0, 1])
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    (tonic, minor), best = ranked[0]
    relative = ((tonic + 3) % 12, False) if minor else ((tonic + 9) % 12, True)
    second = next(score for k, score in ranked[1:] if k != relative)
    margin = best - second
    if best < policy.key_min_correlation:
        unknown["key"] = "low_correlation"
        return None, best
    if margin < policy.key_min_margin:
        unknown["key"] = "ambiguous"
        return None, best
    return camelot_for(tonic, minor), best


def measure_features(candidate_id: str, audio: bytes,
                     policy: FeatureExtractionPolicy = DEFAULT_FEATURE_POLICY) -> TrackFeatures:
    """Measure one track's features from its verified bytes; unknowns are explicit."""
    unknown: dict[str, str] = {"vocal_density": "no_vocal_detector"}
    try:
        samples, rate = sf.read(io.BytesIO(audio), dtype="float64", always_2d=True)
    except (RuntimeError, ValueError, TypeError, sf.LibsndfileError) as exc:
        reason = f"undecodable:{type(exc).__name__}"
        return TrackFeatures(
            candidate_id=candidate_id, bpm=None, bpm_confidence=None, camelot=None,
            key_confidence=None, energy_in=None, energy_out=None, energy_mean=None,
            vocal_density=None, extractor_version=policy.version,
            unknown_reasons={"bpm": reason, "key": reason, "energy": reason, **unknown},
        )
    mono: FloatArray = samples.mean(axis=1)
    factor = max(1, round(rate / _ANALYSIS_RATE))
    usable = len(mono) - len(mono) % factor
    analysis: FloatArray = np.ascontiguousarray(mono[:usable].reshape(-1, factor).mean(axis=1))
    analysis_rate = rate / factor

    energy_in, energy_out, energy_mean = _energy(mono, float(rate), policy, unknown)
    if energy_mean is None:
        unknown.setdefault("bpm", "silent")
        unknown.setdefault("key", "silent")
        bpm = bpm_conf = key_conf = None
        camelot = None
    else:
        bpm, bpm_conf = _tempo(analysis, analysis_rate, policy, unknown)
        camelot, key_conf = _key(analysis, analysis_rate, policy, unknown)
    return TrackFeatures(
        candidate_id=candidate_id, bpm=bpm, bpm_confidence=bpm_conf, camelot=camelot,
        key_confidence=key_conf, energy_in=energy_in, energy_out=energy_out,
        energy_mean=energy_mean, vocal_density=None, extractor_version=policy.version,
        unknown_reasons=unknown,
    )

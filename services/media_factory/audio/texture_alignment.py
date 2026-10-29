"""Diagnostic-only offset search using P6's existing four-window texture feature.

This module is NOT wired into the production P6 release gate. It compares
checksum-verified audio bytes supplied by the caller, sliding the candidate
waveform through a fixed, bounded set of offsets and retaining the best score.
It neither proves musical originality nor corrects tempo/pitch changes.
"""
from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import soundfile as sf  # type: ignore[import-untyped]

from ..playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY, valid_texture_signature
from .features import _ANALYSIS_RATE, _texture_signature

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_DECODED_FRAMES = 12_000_000
OFFSETS_SECONDS: tuple[float, ...] = (-0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75)
PLAYBACK_RATES: tuple[float, ...] = (0.95, 1.0, 1.05)

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class OffsetComparison:
    baseline_score: float
    best_offset_score: float
    best_candidate_shift_seconds: float
    examined_offsets: int
    best_candidate_playback_rate: float = 1.0


def _analysis_signal(data: bytes) -> tuple[FloatArray, float]:
    """Match p6-features-v2 mono/downsample preprocessing with bounded decoding."""
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("source audio exceeds the bounded diagnostic byte limit")
    try:
        with sf.SoundFile(io.BytesIO(data)) as stream:
            if (
                stream.frames < 2048 or stream.frames > MAX_DECODED_FRAMES
                or stream.channels not in (1, 2)
            ):
                raise ValueError("source audio frame/channel count is outside limits")
            samples = stream.read(dtype="float64", always_2d=True)
            rate = stream.samplerate
    except (RuntimeError, sf.LibsndfileError) as exc:
        raise ValueError("cannot decode diagnostic audio") from exc
    mono = samples.mean(axis=1)
    factor = max(1, round(rate / _ANALYSIS_RATE))
    usable = len(mono) - len(mono) % factor
    if usable <= 0:
        raise ValueError("source audio has no usable samples")
    analysis: FloatArray = np.ascontiguousarray(
        mono[:usable].reshape(-1, factor).mean(axis=1)
    )
    return analysis, rate / factor


def _signature(signal: FloatArray, rate: float) -> tuple[float, ...]:
    texture = _texture_signature(signal, rate)
    if not valid_texture_signature(
        texture, bins=DEFAULT_ALBUM_TEXTURE_POLICY.expected_signature_bins
    ):
        raise ValueError("missing or invalid four-window texture signature")
    assert texture is not None
    return texture


def _shift_candidate(signal: FloatArray, samples: int) -> FloatArray:
    """Positive shift trims candidate's leading content, padding only at the edge."""
    if abs(samples) >= len(signal):
        raise ValueError("search offset exceeds candidate length")
    if samples == 0:
        return signal
    aligned = np.zeros_like(signal)
    if samples > 0:
        aligned[:-samples] = signal[samples:]
    else:
        aligned[-samples:] = signal[:samples]
    return aligned


def _restore_playback_rate(signal: FloatArray, factor: float) -> FloatArray:
    """Undo speed-and-pitch resampling of a candidate, not tempo-only stretching."""
    if factor == 1.0:
        return signal
    restored_count = round((len(signal) - 1) * factor) + 1
    positions = np.arange(restored_count, dtype=np.float64) / factor
    restored: FloatArray = np.interp(
        positions, np.arange(len(signal), dtype=np.float64), signal
    )
    return restored


def offset_aligned_texture(
    original: bytes,
    candidate: bytes,
    *,
    offsets_seconds: Sequence[float] = OFFSETS_SECONDS,
    playback_rates: Sequence[float] = (1.0,),
) -> OffsetComparison:
    """Search bounded shifts and optional playback rates; never decide a release gate.

    A playback rate of 1.05 means the candidate was replayed 5% faster,
    changing pitch as well as duration. The default 1.0 preserves the original
    offset-only diagnostic, including its zero-shift baseline.
    """
    if (
        not offsets_seconds or len(offsets_seconds) > len(OFFSETS_SECONDS)
        or len(set(offsets_seconds)) != len(offsets_seconds)
        or 0.0 not in offsets_seconds
        or any(
            not isinstance(offset, (int, float)) or isinstance(offset, bool)
            or not math.isfinite(offset) or abs(offset) > 0.75
            for offset in offsets_seconds
        )
    ):
        raise ValueError("offsets must be distinct finite values within +/-0.75s including 0")
    if (
        not playback_rates or len(playback_rates) > len(PLAYBACK_RATES)
        or len(set(playback_rates)) != len(playback_rates)
        or 1.0 not in playback_rates
        or any(
            not isinstance(rate, (int, float)) or isinstance(rate, bool)
            or not math.isfinite(rate) or rate not in PLAYBACK_RATES
            for rate in playback_rates
        )
    ):
        raise ValueError("playback rates must be distinct values from 0.95, 1.0, 1.05 including 1.0")

    base, base_rate = _analysis_signal(original)
    variant, variant_rate = _analysis_signal(candidate)
    if not math.isclose(base_rate, variant_rate, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("incompatible analysis sample rates")
    if len(base) < round(base_rate * 2) or len(variant) < round(base_rate * 2):
        raise ValueError("audio too short for bounded offset comparison")
    reference = _signature(base, base_rate)
    scores: list[tuple[float, float, float]] = []
    for playback_rate in playback_rates:
        restored = _restore_playback_rate(variant, float(playback_rate))
        for seconds in offsets_seconds:
            shifted = _shift_candidate(restored, round(seconds * variant_rate))
            try:
                measured = _signature(shifted, variant_rate)
            except ValueError:
                if seconds == 0 and playback_rate == 1.0:
                    raise
                continue
            score = math.fsum(
                a * b for a, b in zip(reference, measured, strict=True)
            ) / 4
            if not math.isfinite(score):
                raise ValueError("nonfinite rate/offset texture score")
            scores.append((max(0.0, min(1.0, score)), float(seconds), float(playback_rate)))
    baseline = next(
        (score for score, seconds, rate in scores if seconds == 0 and rate == 1.0),
        None,
    )
    if baseline is None:
        raise ValueError("zero-offset, normal-speed signature could not be measured")
    best = min(
        scores,
        key=lambda value: (-value[0], abs(value[2] - 1.0), abs(value[1]), value[1], value[2]),
    )
    return OffsetComparison(
        baseline_score=baseline,
        best_offset_score=best[0],
        best_candidate_shift_seconds=best[1],
        examined_offsets=len(scores),
        best_candidate_playback_rate=best[2],
    )


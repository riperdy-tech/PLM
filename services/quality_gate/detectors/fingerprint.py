"""Deterministic spectral-landmark audio fingerprint (R4, ADR-0034).

A constellation / peak-pair fingerprint in numpy and scipy, chosen over chromaprint so
that it runs identically on every host with no native tool:

1. Mix to mono, resample (polyphase, exact integer ratio) to ``sample_rate_hz`` and
   peak-normalise, so a gain change does not move any peak.
2. Hann-windowed STFT magnitude in dB.
3. Peaks are local maxima over a (frames x bins) neighbourhood that lie within
   ``peak_floor_db`` of the loudest bin; at most ``max_peaks_per_second`` of the
   strongest survive per one-second block (ties broken by time, then frequency).
4. Each peak (anchor) is paired with up to ``fan_out`` later peaks inside the target
   zone; a hash is ``(f_anchor, f_target, dt)`` packed into an integer, kept with the
   anchor's frame.
5. Matching counts, per reference, candidate hashes that agree on one time offset
   (within ``offset_tolerance_frames``), so a clip cut from a reference at any start
   still lines up.

Limits (ADR-0034, ``docs/STATUS.md``): it finds re-use of a *recording* (same audio,
re-cut, re-gained, lightly noised). It does not find a re-performance, a re-mix with
heavy processing, time-stretching or pitch-shifting, or a composition copied into new
audio, and it knows only the references it is given.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from math import gcd
from pathlib import Path
from typing import Final

import numpy as np
import numpy.typing as npt
import soundfile as sf  # type: ignore[import-untyped]
from scipy.ndimage import maximum_filter  # type: ignore[import-untyped]
from scipy.signal import resample_poly  # type: ignore[import-untyped]

from .config import AudioFingerprintParams
from .outcome import DetectorOutcome, DetectorResult

__all__ = [
    "AUDIO_DETECTOR",
    "AudioFingerprint",
    "AudioUnreadableError",
    "ReferenceFingerprint",
    "fingerprint_file",
    "fingerprint_samples",
    "fingerprint_set_ref",
    "match_fingerprint",
]

AUDIO_DETECTOR: Final[str] = "audio_fingerprint"
_EMPTY: Final = np.zeros((0, 2), dtype=np.int64)


class AudioUnreadableError(ValueError):
    """The audio could not be decoded (missing, corrupt or an unsupported container)."""


@dataclass(frozen=True, slots=True, eq=False)
class AudioFingerprint:
    """Sorted unique ``(hash, anchor_frame)`` rows plus the version they were made under."""

    version: str
    hashes: npt.NDArray[np.int64]
    duration_seconds: float

    @property
    def hash_count(self) -> int:
        return int(self.hashes.shape[0])

    def to_bytes(self) -> bytes:
        return np.ascontiguousarray(self.hashes, dtype="<i8").tobytes()

    @classmethod
    def from_bytes(cls, version: str, blob: bytes, duration_seconds: float) -> AudioFingerprint:
        if len(blob) % 16:
            raise ValueError("fingerprint blob is not a whole number of (hash, frame) rows")
        rows = np.frombuffer(blob, dtype="<i8").astype(np.int64).reshape(-1, 2)
        return cls(version=version, hashes=rows, duration_seconds=duration_seconds)

    def digest(self) -> str:
        return hashlib.sha256(self.version.encode() + b"\0" + self.to_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReferenceFingerprint:
    """One entry of a fingerprint source (internal catalog or reference set)."""

    ref_id: str
    fingerprint: AudioFingerprint
    candidate_id: str | None = None
    """For internal-catalog entries: the candidate it came from (self-matches excluded)."""
    label: str | None = None


def fingerprint_set_ref(entries: Sequence[ReferenceFingerprint]) -> str:
    """Content hash of a fingerprint source: the ordered entry ids and digests."""
    h = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: e.ref_id):
        h.update(entry.ref_id.encode() + b"\0" + entry.fingerprint.digest().encode() + b"\n")
    return f"fpset:{h.hexdigest()}"


def _read_audio(path: str | Path) -> tuple[npt.NDArray[np.float64], int]:
    try:
        data, sample_rate = sf.read(str(path), dtype="float64", always_2d=True)
    except Exception as exc:  # soundfile raises several types; all mean "unreadable"
        raise AudioUnreadableError(f"{type(exc).__name__}: {exc}") from exc
    if data.size == 0:
        raise AudioUnreadableError("the audio has no samples")
    return np.asarray(data, dtype=np.float64), int(sample_rate)


def fingerprint_file(path: str | Path, params: AudioFingerprintParams) -> AudioFingerprint:
    """Fingerprint an audio file soundfile can decode (WAV, FLAC, OGG)."""
    samples, sample_rate = _read_audio(path)
    return fingerprint_samples(samples, sample_rate, params)


def fingerprint_samples(
    samples: npt.ArrayLike, sample_rate: int, params: AudioFingerprintParams
) -> AudioFingerprint:
    """Fingerprint raw samples (``(n,)`` mono or ``(n, channels)``)."""
    x = np.nan_to_num(np.asarray(samples, dtype=np.float64))
    if x.ndim == 2:
        x = x.mean(axis=1)
    if x.ndim != 1:
        raise ValueError("samples must be (n,) or (n, channels)")
    duration = float(x.shape[0]) / float(sample_rate)
    target = params.sample_rate_hz
    if sample_rate != target:
        g = gcd(int(sample_rate), target)
        x = resample_poly(x, target // g, int(sample_rate) // g)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak < 1e-9 or x.shape[0] < params.n_fft:
        return AudioFingerprint(params.fingerprint_version, _EMPTY, duration)
    x = x / peak

    n_frames = 1 + (x.shape[0] - params.n_fft) // params.hop_length
    index = (
        np.arange(params.n_fft)[None, :] + params.hop_length * np.arange(n_frames)[:, None]
    )
    frames = x[index] * np.hanning(params.n_fft)[None, :]
    magnitude_db = 20.0 * np.log10(np.abs(np.fft.rfft(frames, axis=1)) + 1e-10)

    neighbourhood = maximum_filter(
        magnitude_db,
        size=(params.peak_neighbourhood_frames, params.peak_neighbourhood_bins),
        mode="constant",
        cval=-np.inf,
    )
    is_peak = (magnitude_db == neighbourhood) & (
        magnitude_db >= magnitude_db.max() - params.peak_floor_db
    )
    times, freqs = np.nonzero(is_peak)
    if times.size == 0:
        return AudioFingerprint(params.fingerprint_version, _EMPTY, duration)
    values = magnitude_db[times, freqs]

    # Density cap: the strongest peaks per one-second block, deterministic tie-break.
    block = (times * params.hop_length) // target
    order = np.lexsort((freqs, times, -values, block))
    sorted_blocks = block[order]
    rank = np.arange(order.size) - np.searchsorted(sorted_blocks, sorted_blocks, side="left")
    kept = order[rank < params.max_peaks_per_second]
    kept = kept[np.lexsort((freqs[kept], times[kept]))]
    peak_t = times[kept].astype(np.int64)
    peak_f = freqs[kept].astype(np.int64)

    rows: list[tuple[int, int]] = []
    for a in range(peak_t.size):
        t_a = int(peak_t[a])
        f_a = int(peak_f[a])
        stop = int(np.searchsorted(peak_t, t_a + params.pair_dt_max_frames, side="right"))
        paired = 0
        for b in range(a + 1, stop):
            dt = int(peak_t[b]) - t_a
            if dt < params.pair_dt_min_frames:
                continue
            f_b = int(peak_f[b])
            if abs(f_b - f_a) > params.pair_df_max_bins:
                continue
            rows.append(((f_a << 16) | (f_b << 6) | dt, t_a))
            paired += 1
            if paired >= params.fan_out:
                break
    if not rows:
        return AudioFingerprint(params.fingerprint_version, _EMPTY, duration)
    hashes = np.unique(np.asarray(rows, dtype=np.int64), axis=0)
    return AudioFingerprint(params.fingerprint_version, hashes, duration)


def best_alignment(refs: npt.NDArray[np.int64], offsets: npt.NDArray[np.int64],
                   tolerance: int) -> tuple[int, int, int]:
    """The (reference, offset) with the most hits within ``tolerance`` frames of it.

    ``refs``/``offsets`` hold one row per candidate hash found in a reference. Returns
    ``(aligned_count, reference, offset)``; ties go to the smallest (reference, offset),
    and ``(0, -1, 0)`` when there is no hit. Vectorised over the distinct
    (reference, offset) pairs: the per-pair Python loop made every evaluation scale with
    the number of hits against the whole internal catalog (R5: minutes per candidate
    once the catalog held a few hundred recordings).
    """
    if refs.size == 0:
        return 0, -1, 0
    low = int(offsets.min())
    width = int(offsets.max()) - low + 2 * tolerance + 1
    keys = refs.astype(np.int64) * width + (offsets.astype(np.int64) - low + tolerance)
    unique, counts = np.unique(keys, return_counts=True)  # sorted = (ref, offset) order
    aligned = np.zeros(unique.size, dtype=np.int64)
    for delta in range(-tolerance, tolerance + 1):
        target = unique + delta
        at = np.searchsorted(unique, target)
        inside = at < unique.size
        hit = np.zeros(unique.size, dtype=bool)
        hit[inside] = unique[at[inside]] == target[inside]
        aligned[hit] += counts[at[hit]]
    best = int(np.argmax(aligned))  # the first maximum: the smallest (ref, offset)
    key = int(unique[best])
    return int(aligned[best]), key // width, key % width + low - tolerance


def match_fingerprint(
    candidate: AudioFingerprint,
    entries: Sequence[ReferenceFingerprint],
    params: AudioFingerprintParams,
    *,
    source: str,
    thresholds_version: str,
    snapshot_ref: str,
    empty_source_is_unknown: bool,
) -> DetectorResult:
    """Compare one candidate against every entry of one source.

    ``empty_source_is_unknown``: a reference set with no entries proves nothing and is
    ``UNKNOWN``; the internal catalog legitimately starts empty and is then ``NO_MATCH``.
    An entry made under another fingerprint version makes the source ``UNKNOWN`` (it
    cannot be compared, and skipping it would silently shrink the source).
    """

    def result(
        outcome: DetectorOutcome,
        *,
        metrics: dict[str, object],
        score: float | None = None,
        matched_ref: str | None = None,
        reason: str | None = None,
    ) -> DetectorResult:
        return DetectorResult(
            detector=AUDIO_DETECTOR,
            source=source,
            outcome=outcome,
            version=params.fingerprint_version,
            thresholds_version=thresholds_version,
            score=score,
            matched_ref=matched_ref,
            source_snapshot_ref=snapshot_ref,
            reason=reason,
            metrics=metrics,
        )

    base_metrics: dict[str, object] = {
        "candidate_hashes": candidate.hash_count,
        "entries": len(entries),
        "match_min_aligned_hashes": params.match_min_aligned_hashes,
        "match_min_score": params.match_min_score,
    }
    if candidate.version != params.fingerprint_version:
        return result(DetectorOutcome.UNKNOWN, reason="candidate_version_mismatch",
                      metrics=base_metrics)
    stale = sorted(e.ref_id for e in entries if e.fingerprint.version != params.fingerprint_version)
    if stale:
        return result(
            DetectorOutcome.UNKNOWN,
            reason="fingerprint_version_mismatch",
            metrics={**base_metrics, "stale_entries": stale[:20], "stale_count": len(stale)},
        )
    if candidate.hash_count < params.min_candidate_hashes:
        return result(DetectorOutcome.UNKNOWN, reason="insufficient_candidate_hashes",
                      metrics=base_metrics)
    usable = [e for e in entries if e.fingerprint.hash_count > 0]
    if not usable:
        if empty_source_is_unknown:
            return result(DetectorOutcome.UNKNOWN, reason="source_empty", metrics=base_metrics)
        return result(DetectorOutcome.NO_MATCH, score=0.0, metrics=base_metrics)

    ordered = sorted(usable, key=lambda e: e.ref_id)
    ref_hash = np.concatenate([e.fingerprint.hashes[:, 0] for e in ordered])
    ref_time = np.concatenate([e.fingerprint.hashes[:, 1] for e in ordered])
    ref_index = np.concatenate(
        [np.full(e.fingerprint.hash_count, i, dtype=np.int64) for i, e in enumerate(ordered)]
    )
    by_hash = np.argsort(ref_hash, kind="stable")
    ref_hash, ref_time, ref_index = ref_hash[by_hash], ref_time[by_hash], ref_index[by_hash]

    cand_hash = candidate.hashes[:, 0]
    cand_time = candidate.hashes[:, 1]
    left = np.searchsorted(ref_hash, cand_hash, side="left")
    right = np.searchsorted(ref_hash, cand_hash, side="right")
    counts = right - left
    total = int(counts.sum())

    best_count = 0
    best_index = -1
    best_offset = 0
    if total:
        cand_rows = np.repeat(np.arange(cand_hash.size), counts)
        starts = np.repeat(left, counts)
        run_start = np.repeat(np.cumsum(counts) - counts, counts)
        ref_rows = starts + (np.arange(total) - run_start)
        offsets = ref_time[ref_rows] - cand_time[cand_rows]
        refs = ref_index[ref_rows]
        best_count, best_index, best_offset = best_alignment(
            refs, offsets, params.offset_tolerance_frames)

    if best_index < 0:
        return result(DetectorOutcome.NO_MATCH, score=0.0, metrics=base_metrics)
    best = ordered[best_index]
    denominator = min(candidate.hash_count, best.fingerprint.hash_count)
    score = best_count / denominator if denominator else 0.0
    is_match = (
        best_count >= params.match_min_aligned_hashes and score >= params.match_min_score
    )
    metrics = {
        **base_metrics,
        "aligned_hashes": best_count,
        "best_ref": best.ref_id,
        "best_ref_hashes": best.fingerprint.hash_count,
        "best_ref_label": best.label,
        "best_ref_candidate_id": best.candidate_id,
        "offset_seconds": round(best_offset * params.hop_length / params.sample_rate_hz, 3),
    }
    return result(
        DetectorOutcome.MATCH if is_match else DetectorOutcome.NO_MATCH,
        score=round(score, 6),
        matched_ref=best.ref_id if is_match else None,
        metrics=metrics,
    )


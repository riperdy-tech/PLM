"""Read-only PCM spot checks of local album-master audio against source WAVs.

Reports descriptive time-aligned normalized waveform correlation only.
It does not identify licensed sources or verify remote/public video content.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

_CLIP_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_WAV_NAME = re.compile(r"track_[0-9]{2}_([0-9a-f]{8})\.wav\Z")
_MAX_TRACKS = 100
_MAX_BYTES = 4_000_000_000


def _valid_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        math.isfinite(value) and value >= 0
    )


def _waveform(path: Path, seconds: float, frame_count: int, rate: int) -> np.ndarray:
    with sf.SoundFile(str(path)) as wav:
        wav.seek(round(seconds * rate))
        segment = wav.read(frame_count, dtype="float32", always_2d=True)
    if len(segment) != frame_count or not np.isfinite(segment).all():
        raise ValueError("unreadable_audio_segment")
    return np.asarray(segment.mean(axis=1), dtype=np.float64)


def _correlation(
    master: Path,
    source: Path,
    rate: int,
    position: float,
    source_offset: float,
    *,
    window_seconds: float,
    tolerance_seconds: float,
    master_duration: float,
) -> dict[str, float | None]:
    expected = position + source_offset
    start = max(0.0, expected - tolerance_seconds)
    span = expected + window_seconds + tolerance_seconds - start
    if start + span > master_duration + 0.0001:
        return {"correlation": None, "time_delta_seconds": None}
    snippet_frames = round(window_seconds * rate)
    search_frames = round(span * rate)
    try:
        needle = _waveform(source, source_offset, snippet_frames, rate)
        haystack = _waveform(master, start, search_frames, rate)
    except (OSError, RuntimeError, ValueError):
        return {"correlation": None, "time_delta_seconds": None}
    return aligned_pcm_correlation(
        needle, haystack, search_start=start, expected_start=expected, rate=rate,
    )


def aligned_pcm_correlation(
    reference: np.ndarray, candidate: np.ndarray, *,
    search_start: float, expected_start: float, rate: int,
) -> dict[str, float | None]:
    """Peak normalized mono PCM correlation, including an offset measurement.

    Both inputs are bounded decoded audio windows with the same sample rate.
    An undefined/silent comparison returns null values, never a passing score.
    """
    if (
        rate <= 0 or reference.ndim != 1 or candidate.ndim != 1
        or len(reference) == 0 or len(candidate) < len(reference)
        or not np.isfinite(reference).all() or not np.isfinite(candidate).all()
    ):
        return {"correlation": None, "time_delta_seconds": None}
    needle = np.asarray(reference, dtype=np.float64) - float(reference.mean())
    haystack = np.asarray(candidate, dtype=np.float64) - float(candidate.mean())
    power = float(needle @ needle)
    if power <= 1e-8:
        return {"correlation": None, "time_delta_seconds": None}
    numerators = signal.correlate(haystack, needle, mode="valid", method="fft")
    squared = np.concatenate(([0.0], np.cumsum(haystack * haystack)))
    local_power = np.maximum(0.0, squared[len(needle):] - squared[:-len(needle)])
    denominators = np.sqrt(local_power * power)
    scores = np.full(len(numerators), -1.0)
    np.divide(numerators, denominators, out=scores, where=denominators > 1e-8)
    if not np.any(denominators > 1e-8):
        return {"correlation": None, "time_delta_seconds": None}
    best = int(np.argmax(scores))
    return {
        "correlation": round(float(np.clip(scores[best], -1.0, 1.0)), 6),
        "time_delta_seconds": round(search_start + best / rate - expected_start, 4),
    }


def probe_album_master(
    album_folder: Path, *,
    window_seconds: float = 2.0,
    tolerance_seconds: float = 0.6,
) -> dict[str, object]:
    if not (0.5 <= window_seconds <= 4.0 and 0.0 <= tolerance_seconds <= 2.0):
        raise ValueError("invalid_window_settings")
    if not album_folder.is_dir() or album_folder.is_symlink():
        raise ValueError("invalid_album_directory")
    summary_file = album_folder / "publication_summary.json"
    tracks_dir = album_folder / "tracks"
    if summary_file.is_symlink() or not summary_file.is_file():
        raise ValueError("publication_summary_unavailable")
    if tracks_dir.is_symlink() or not tracks_dir.is_dir():
        raise ValueError("source_audio_directory_unavailable")
    try:
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("publication_summary_invalid") from exc
    if not isinstance(summary, dict) or not isinstance(summary.get("tracks"), list):
        raise TypeError("publication_summary_invalid")
    rows = summary["tracks"]
    if not 1 <= len(rows) <= _MAX_TRACKS:
        raise ValueError("manifest_track_limit")

    wavs = sorted(tracks_dir.glob("*.wav"))
    if len(wavs) > _MAX_TRACKS:
        raise ValueError("audio_track_limit")
    by_prefix: dict[str, list[Path]] = {}
    for wav in wavs:
        if wav.is_symlink() or not wav.is_file() or wav.stat().st_size > _MAX_BYTES:
            raise ValueError("unsafe_source_audio")
        found = _WAV_NAME.fullmatch(wav.name)
        if found:
            by_prefix.setdefault(found.group(1), []).append(wav)
    valid_ids = [
        str(row.get("clip_id")) for row in rows
        if isinstance(row, dict) and isinstance(row.get("clip_id"), str)
        and _CLIP_UUID.fullmatch(row["clip_id"])
    ]
    prefix_frequency = Counter(clip[:8] for clip in valid_ids)
    master_candidates = [
        album_folder / "album_master_continuous.wav",
        album_folder / "album_master.wav",
    ]
    master = next((p for p in master_candidates if p.is_file() and not p.is_symlink()), None)
    if master is None or master.stat().st_size > _MAX_BYTES:
        raise ValueError("album_master_unavailable")
    try:
        master_info = sf.info(str(master))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("album_master_unreadable") from exc
    if (
        not 8000 <= master_info.samplerate <= 96000
        or not 1 <= master_info.channels <= 2
        or master_info.duration > 7200
    ):
        raise ValueError("album_master_format_unsupported")

    results: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        result: dict[str, object] = {
            "manifest_index": index,
            "local_filename_index": None,
            "status": "unknown",
            "windows": [],
        }
        results.append(result)
        if not isinstance(row, dict):
            result["status"] = "invalid_manifest_row"
            continue
        clip = row.get("clip_id")
        start = row.get("start_seconds")
        length = row.get("duration_seconds")
        if (
            row.get("index") != index or isinstance(row.get("index"), bool)
            or not isinstance(clip, str) or not _CLIP_UUID.fullmatch(clip)
            or not _valid_number(start) or not _valid_number(length)
            or length < 16
        ):
            result["status"] = "invalid_or_short_manifest_row"
            continue
        candidates = by_prefix.get(clip[:8], [])
        if len(candidates) != 1 or prefix_frequency[clip[:8]] != 1:
            result["status"] = "source_prefix_ambiguous" if candidates else "source_prefix_absent"
            continue
        source = candidates[0]
        result["local_filename_index"] = int(source.name[6:8])
        try:
            info = sf.info(str(source))
        except (OSError, RuntimeError, ValueError):
            result["status"] = "source_unreadable"
            continue
        if (
            info.samplerate != master_info.samplerate
            or info.channels != master_info.channels
            or abs(info.duration - length) > 0.5
        ):
            result["status"] = "source_format_or_duration_mismatch"
            continue
        offsets = [8.0, min(length - 5.0, max(12.0, length * 0.6))]
        if offsets[1] - offsets[0] < 4:
            result["status"] = "insufficient_windows"
            continue
        results_windows = [
            _correlation(
                master, source, master_info.samplerate, start, offset,
                window_seconds=window_seconds,
                tolerance_seconds=tolerance_seconds,
                master_duration=master_info.duration,
            )
            for offset in offsets
        ]
        result["windows"] = results_windows
        result["status"] = (
            "measured" if all(w["correlation"] is not None for w in results_windows)
            else "unavailable_window"
        )
    return {
        "evaluation": "read_only_local_master_pcm_correlation_not_rights_or_publication",
        "manifest_track_count": len(rows),
        "measured_track_count": sum(row["status"] == "measured" for row in results),
        "measurements": results,
        "rights_verified": False,
        "remote_publication_verified": False,
        "independent_provenance_verified": False,
        "limitations": [
            "samples two short middle sections per track, not the whole recording",
            "waveform correlation is descriptive, not a calibrated clearance threshold",
            "mastering, resampling or alternate encoding may lower measured correlation",
            "clip identity is only inferred from eight characters in the local filename",
            "local album WAV is not proof of the video or YouTube playback audio",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local album master spot check")
    parser.add_argument("--album-dir", required=True)
    args = parser.parse_args(argv)
    try:
        report = probe_album_master(Path(args.album_dir))
    except (TypeError, ValueError) as exc:
        parser.exit(2, f"Local master inspection refused: {exc}\n")
    except OSError:
        parser.exit(2, "Local master inspection refused: local_file_io_error\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

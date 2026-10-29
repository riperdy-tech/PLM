"""Read-only comparison of decoded local MP4 audio and its local album master.

Only small audio windows are decoded. No streaming, publishing, uploads or rights
decisions. Correlation is descriptive and not an approval threshold.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

# Permit both python scripts/probe_local_mp4_audio.py and python -m scripts....
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.probe_local_album_master import aligned_pcm_correlation

TARGET_RATE = 8000
MAX_VIDEO_BYTES = 8_000_000_000
MAX_TRACKS = 100
MAX_DURATION_SECONDS = 7200
DECODER_TIMEOUT_SECONDS = 45
MAX_TAIL_PACKETS = 20_000


def _number(value: object) -> float | None:
    try:
        parsed = float(value) if not isinstance(value, bool) else float("nan")
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _probe_streams(path: Path) -> tuple[dict[str, object], dict[str, object] | None, float | None]:
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries",
                ("stream=index,codec_name,codec_type,sample_rate,channels,start_time,duration:"
                 "format=duration"), "-of", "json", str(path),
            ],
            check=False, capture_output=True, text=True, timeout=DECODER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("local_video_probe_unavailable") from exc
    if completed.returncode != 0:
        raise ValueError("local_video_probe_failed")
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise ValueError("local_video_probe_invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise TypeError("local_video_probe_invalid")
    streams = payload["streams"]
    audios = [
        s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"
    ]
    videos = [
        s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"
    ]
    if len(audios) != 1 or len(videos) > 1:
        raise ValueError("local_video_audio_stream_ambiguous_or_missing")
    return audios[0], videos[0] if videos else None, _number(
        payload.get("format", {}).get("duration")
        if isinstance(payload.get("format"), dict) else None
    )


def _tail_packets(
    path: Path, stream: str, *, scan_from: float, scan_span: float,
) -> list[tuple[float, float]] | None:
    """Read bounded local packet PTS/duration; missing evidence stays unknown."""
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", stream,
                "-read_intervals", f"{scan_from:.3f}%+{scan_span:.3f}",
                "-show_entries", "packet=pts_time,duration_time",
                "-of", "json", str(path),
            ],
            check=False, capture_output=True, text=True, timeout=DECODER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or len(completed.stdout) > 3_000_000:
        return None
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("packets"), list):
        return None
    packets = payload["packets"]
    if not packets or len(packets) > MAX_TAIL_PACKETS:
        return None
    measured: list[tuple[float, float]] = []
    for packet in packets:
        if not isinstance(packet, dict):
            return None
        pts, duration = _number(packet.get("pts_time")), _number(packet.get("duration_time"))
        if pts is None or duration is None or duration <= 0:
            return None
        measured.append((pts, pts + duration))
    return measured


def _inspect_packet_tail(
    path: Path, *, audio_start: float, audio_duration: float,
    video_duration: float | None,
) -> dict[str, object]:
    """Confirm final on-disk packet timing, not a player's playback behavior."""
    unknown: dict[str, object] = {
        "status": "unknown",
        "last_audio_packet_end_seconds": None,
        "last_video_packet_end_seconds": None,
        "video_packets_starting_after_audio_end_count": None,
        "packet_end_gap_seconds": None,
    }
    if video_duration is None:
        return {**unknown, "status": "no_video_stream"}
    audio_end = audio_start + audio_duration
    if audio_end > MAX_DURATION_SECONDS or video_duration > MAX_DURATION_SECONDS:
        return unknown
    # Seeking can return a preceding video keyframe (rather than scan_from).
    # Sufficiently long bounded scan spans cover ordinary GOPs; if the end
    # is not observed we return unknown, never a false clean conclusion.
    audio = _tail_packets(
        path, "a:0", scan_from=max(0.0, audio_end - 12.0), scan_span=50.0,
    )
    video = _tail_packets(
        path, "v:0", scan_from=max(0.0, audio_end - 90.0), scan_span=240.0,
    )
    if audio is None or video is None:
        return unknown
    last_audio = max(end for _, end in audio)
    last_video = max(end for _, end in video)
    if abs(last_audio - audio_end) > 0.25 or abs(last_video - video_duration) > 1.0:
        return unknown
    after_count = sum(start >= last_audio - 0.0005 for start, _ in video)
    gap = last_video - last_audio
    return {
        "status": (
            "video_packets_continue_after_audio"
            if after_count and gap > 0.05 else "no_confirmed_video_only_tail"
        ),
        "last_audio_packet_end_seconds": round(last_audio, 6),
        "last_video_packet_end_seconds": round(last_video, 6),
        "video_packets_starting_after_audio_end_count": after_count,
        "packet_end_gap_seconds": round(gap, 6),
    }


def require_local_render_audio_sync(
    video: Path, *, expected_audio_seconds: float, tolerance_seconds: float = 0.75,
) -> dict[str, object]:
    """Fail-closed local render handoff check; never grants publish approval.

    Checks stream duration against the original WAV and requires independently
    observed final audio/video packets. Raises when timing cannot be established.
    """
    if (
        not math.isfinite(expected_audio_seconds) or expected_audio_seconds <= 0
        or not 0 < tolerance_seconds <= 2.0
        or video.is_symlink() or not video.is_file()
        or video.stat().st_size > MAX_VIDEO_BYTES
    ):
        raise ValueError("local_render_timing_inputs_invalid")
    audio, visual, container_duration = _probe_streams(video)
    if visual is None:
        raise ValueError("local_render_video_stream_missing")
    audio_duration = _number(audio.get("duration"))
    audio_start = _number(audio.get("start_time"))
    video_duration = _number(visual.get("duration"))
    if audio_duration is None or audio_start is None or video_duration is None:
        raise ValueError("local_render_stream_timing_unknown")
    audio_end = audio_start + audio_duration
    if abs(audio_duration - expected_audio_seconds) > tolerance_seconds:
        raise ValueError("local_render_audio_duration_mismatch")
    if abs(video_duration - audio_end) > tolerance_seconds:
        raise ValueError("local_render_video_audio_duration_mismatch")
    packet_tail = _inspect_packet_tail(
        video, audio_start=audio_start, audio_duration=audio_duration,
        video_duration=video_duration,
    )
    if packet_tail["status"] != "no_confirmed_video_only_tail":
        raise ValueError("local_render_packet_tail_not_clear")
    gap = packet_tail["packet_end_gap_seconds"]
    if not isinstance(gap, (float, int)) or abs(gap) > tolerance_seconds:
        raise ValueError("local_render_packet_gap_out_of_range")
    if container_duration is None or abs(container_duration - audio_end) > tolerance_seconds:
        raise ValueError("local_render_container_duration_mismatch")
    return {
        "status": "locally_timing_consistent_only",
        "audio_stream_duration_seconds": audio_duration,
        "video_stream_duration_seconds": video_duration,
        "packet_end_gap_seconds": gap,
        "remote_publication_verified": False,
        "rights_verified": False,
    }


def _decoded_audio_window(path: Path, start: float, length: float) -> np.ndarray | None:
    try:
        completed = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-ss", f"{start:.6f}", "-i", str(path), "-map", "0:a:0",
                "-vn", "-ac", "1", "-ar", str(TARGET_RATE),
                "-t", f"{length:.6f}", "-f", "f32le", "pipe:1",
            ],
            check=False, capture_output=True, timeout=DECODER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or len(completed.stdout) % 4 != 0:
        return None
    samples = np.frombuffer(completed.stdout, dtype="<f4")
    expected = round(length * TARGET_RATE)
    if len(samples) < expected - 8 or not np.isfinite(samples).all():
        return None
    return np.asarray(samples[:expected], dtype=np.float64)


def _master_audio_window(
    wav: sf.SoundFile, start: float, length: float,
) -> np.ndarray | None:
    frames = round(length * wav.samplerate)
    wav.seek(round(start * wav.samplerate))
    samples = wav.read(frames, dtype="float32", always_2d=True)
    if len(samples) != frames or not np.isfinite(samples).all():
        return None
    mono = np.asarray(samples.mean(axis=1), dtype=np.float64)
    reduced = math.gcd(wav.samplerate, TARGET_RATE)
    output = signal.resample_poly(mono, TARGET_RATE // reduced, wav.samplerate // reduced)
    frames_target = round(length * TARGET_RATE)
    if len(output) < frames_target:
        return None
    return output[:frames_target]


def probe_local_mp4_audio(
    album_folder: Path, *,
    window_seconds: float = 2.0,
    tolerance_seconds: float = 0.45,
) -> dict[str, object]:
    """Compare locally decoded AAC samples with a local WAV, without writes."""
    if not (0.5 <= window_seconds <= 4.0 and 0 <= tolerance_seconds <= 1.5):
        raise ValueError("invalid_window_settings")
    if not album_folder.is_dir() or album_folder.is_symlink():
        raise ValueError("album_directory_unavailable")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("local_media_decoder_unavailable")
    videos = sorted(album_folder.glob("*.mp4"))
    if len(videos) != 1:
        raise ValueError("local_video_ambiguous_or_unavailable")
    video = videos[0]
    if video.is_symlink() or not video.is_file() or video.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError("local_video_unsafe")
    masters = [
        album_folder / "album_master_continuous.wav",
        album_folder / "album_master.wav",
    ]
    master = next((p for p in masters if p.is_file() and not p.is_symlink()), None)
    if master is None:
        raise ValueError("local_album_master_unavailable")
    summary = album_folder / "publication_summary.json"
    if summary.is_symlink() or not summary.is_file():
        raise ValueError("publication_summary_unavailable")
    try:
        document = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("publication_summary_invalid") from exc
    if not isinstance(document, dict) or not isinstance(document.get("tracks"), list):
        raise TypeError("publication_summary_invalid")
    tracks = document["tracks"]
    if not 1 <= len(tracks) <= MAX_TRACKS:
        raise ValueError("invalid_track_count")

    audio, visual, container_duration = _probe_streams(video)
    audio_duration = _number(audio.get("duration"))
    audio_start = _number(audio.get("start_time"))
    visual_duration = _number(visual.get("duration")) if visual is not None else None
    if audio_duration is None or audio_start is None:
        raise ValueError("local_audio_timing_unavailable")
    try:
        with sf.SoundFile(str(master)) as wav:
            if (
                not 8000 <= wav.samplerate <= 96000
                or not 1 <= wav.channels <= 2
                or wav.frames <= 0 or wav.frames / wav.samplerate > MAX_DURATION_SECONDS
            ):
                raise ValueError("album_master_format_unsupported")
            master_duration = wav.frames / wav.samplerate
            observations: list[dict[str, object]] = []
            for index, row in enumerate(tracks, start=1):
                item: dict[str, object] = {
                    "manifest_index": index, "status": "unknown", "windows": [],
                }
                observations.append(item)
                if not isinstance(row, dict):
                    item["status"] = "invalid_manifest_row"
                    continue
                position = _number(row.get("start_seconds"))
                duration = _number(row.get("duration_seconds"))
                if (
                    row.get("index") != index or isinstance(row.get("index"), bool)
                    or position is None or duration is None or duration < 16
                ):
                    item["status"] = "invalid_or_short_manifest_row"
                    continue
                offsets = [8.0, min(duration - 5.0, max(12.0, duration * 0.6))]
                if offsets[1] - offsets[0] < 4:
                    item["status"] = "insufficient_windows"
                    continue
                windows: list[dict[str, float | None]] = []
                for offset in offsets:
                    expected = position + offset
                    start = max(0.0, audio_start + expected - tolerance_seconds)
                    length = audio_start + expected + window_seconds + tolerance_seconds - start
                    if (
                        expected + window_seconds > master_duration
                        or expected + window_seconds + tolerance_seconds > audio_duration
                    ):
                        windows.append({"correlation": None, "time_delta_seconds": None})
                        continue
                    reference = _master_audio_window(wav, expected, window_seconds)
                    candidate = _decoded_audio_window(video, start, length)
                    if reference is None or candidate is None:
                        windows.append({"correlation": None, "time_delta_seconds": None})
                        continue
                    windows.append(aligned_pcm_correlation(
                        reference, candidate, search_start=start,
                        expected_start=audio_start + expected, rate=TARGET_RATE,
                    ))
                item["windows"] = windows
                item["status"] = (
                    "measured" if all(x["correlation"] is not None for x in windows)
                    else "audio_window_unavailable"
                )
    except (OSError, RuntimeError) as exc:
        raise ValueError("local_master_read_failed") from exc
    return {
        "evaluation": "local_mp4_decoded_audio_pcm_spot_check_only",
        "manifest_track_count": len(tracks),
        "measured_track_count": sum(x["status"] == "measured" for x in observations),
        "measurements": observations,
        "audio_codec": audio.get("codec_name"),
        "audio_stream_duration_seconds": audio_duration,
        "master_wav_duration_seconds": round(master_duration, 6),
        "video_stream_duration_seconds": visual_duration,
        "container_duration_seconds": container_duration,
        "packet_tail": _inspect_packet_tail(
            video, audio_start=audio_start, audio_duration=audio_duration,
            video_duration=visual_duration,
        ),
        "container_minus_audio_seconds": (
            round(container_duration - (audio_start + audio_duration), 6)
            if container_duration is not None else None
        ),
        "video_minus_audio_seconds": (
            round(visual_duration - (audio_start + audio_duration), 6)
            if visual_duration is not None else None
        ),
        "remote_publication_verified": False,
        "rights_verified": False,
        "source_provenance_verified": False,
        "limitations": [
            "two short windows per track do not prove whole-track equivalence",
            "compressed audio correlation has no calibrated approval threshold",
            "local MP4 container and audio stream may have different durations",
            "local video is not the remotely served YouTube video",
            "no listening panel, independent original source or rights were checked",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local MP4/WAV audio probe")
    parser.add_argument("--album-dir", required=True)
    args = parser.parse_args(argv)
    try:
        report = probe_local_mp4_audio(Path(args.album_dir))
    except (TypeError, ValueError) as exc:
        parser.exit(2, f"MP4 audio inspection refused: {exc}\n")
    except OSError:
        parser.exit(2, "MP4 audio inspection refused: local_media_io_error\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Synthetic, local-only MP4 AAC/album WAV comparisons, never remote uploads."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from scripts.probe_local_mp4_audio import main, probe_local_mp4_audio

FFMPEG_PRESENT = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
RATE = 16000


def _album(
    folder: Path, *, unrelated_video: bool = False,
    video_tail_seconds: float = 0.0,
) -> None:
    folder.mkdir()
    rng = np.random.default_rng(12)
    raw = rng.normal(size=RATE * 40)
    filtered = signal.sosfilt(signal.butter(4, 3000, fs=RATE, output="sos"), raw)
    source = np.asarray(filtered * (0.22 / max(abs(filtered))), dtype=np.float32)
    master = folder / "album_master_continuous.wav"
    sf.write(master, source, RATE, subtype="PCM_16")
    if unrelated_video:
        other = folder / "unrelated.wav"
        wrong = np.random.default_rng(121).normal(size=RATE * 40).astype("float32")
        sf.write(other, wrong * 0.1, RATE, subtype="PCM_16")
        video_source = other
    else:
        video_source = master
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if video_tail_seconds:
        command.extend([
            "-f", "lavfi", "-i",
            f"color=c=black:s=64x64:r=2:d={40.0 + video_tail_seconds}",
            "-i", str(video_source), "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-t", str(40.0 + video_tail_seconds),
        ])
    else:
        command.extend(["-i", str(video_source)])
    command.extend(["-c:a", "aac", "-b:a", "128k", str(folder / "sample.mp4")])
    subprocess.run(command, check=True, capture_output=True, timeout=90)
    data = {
        "track_count": 2,
        "tracks": [
            {"index": 1, "start_seconds": 0.0, "duration_seconds": 20.0},
            {"index": 2, "start_seconds": 20.0, "duration_seconds": 20.0},
        ],
    }
    (folder / "publication_summary.json").write_text(
        json.dumps(data), encoding="utf-8",
    )


@pytest.mark.skipif(not FFMPEG_PRESENT, reason="local FFmpeg required")
def test_decoded_aac_matches_local_master_even_after_codec_conversion(
    tmp_path: Path,
) -> None:
    album = tmp_path / "album"
    _album(album)
    result = probe_local_mp4_audio(album)
    assert result["evaluation"] == "local_mp4_decoded_audio_pcm_spot_check_only"
    assert result["manifest_track_count"] == 2
    assert result["measured_track_count"] == 2
    assert result["audio_codec"] == "aac"
    assert result["video_stream_duration_seconds"] is None  # audio-only fixture
    assert result["packet_tail"]["status"] == "no_video_stream"
    assert result["packet_tail"]["video_packets_starting_after_audio_end_count"] is None
    assert all(
        w["correlation"] > 0.85
        for row in result["measurements"]
        for w in row["windows"]
    )
    assert result["remote_publication_verified"] is False
    assert result["rights_verified"] is False
    assert result["source_provenance_verified"] is False
    encoded = json.dumps(result)
    assert "album_master_continuous.wav" not in encoded
    assert str(tmp_path) not in encoded


@pytest.mark.skipif(not FFMPEG_PRESENT, reason="local FFmpeg required")
def test_different_aac_track_is_descriptive_not_false_verified(
    tmp_path: Path,
) -> None:
    album = tmp_path / "album"
    _album(album, unrelated_video=True)
    result = probe_local_mp4_audio(album)
    assert result["measured_track_count"] == 2
    assert all(
        w["correlation"] < 0.20
        for row in result["measurements"]
        for w in row["windows"]
    )
    assert result["remote_publication_verified"] is False


@pytest.mark.skipif(not FFMPEG_PRESENT, reason="local FFmpeg required")
def test_manifest_outside_audio_stream_returns_unknown(
    tmp_path: Path,
) -> None:
    album = tmp_path / "album"
    _album(album)
    summary = album / "publication_summary.json"
    data = json.loads(summary.read_text(encoding="utf-8"))
    data["tracks"][1]["start_seconds"] = 39.0
    summary.write_text(json.dumps(data), encoding="utf-8")
    result = probe_local_mp4_audio(album)
    assert result["measured_track_count"] == 1
    assert result["measurements"][1]["status"] == "audio_window_unavailable"
    assert all(
        w["correlation"] is None for w in result["measurements"][1]["windows"]
    )


@pytest.mark.skipif(not FFMPEG_PRESENT, reason="local FFmpeg required")
def test_cli_omits_local_file_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    album = tmp_path / "private_source"
    _album(album)
    assert main(["--album-dir", str(album)]) == 0
    payload = capsys.readouterr().out
    assert json.loads(payload)["measured_track_count"] == 2
    assert str(tmp_path) not in payload


@pytest.mark.skipif(not FFMPEG_PRESENT, reason="local FFmpeg required")
def test_actual_video_packets_outlast_aac_and_are_counted(tmp_path: Path) -> None:
    album = tmp_path / "album"
    _album(album, video_tail_seconds=4.0)
    report = probe_local_mp4_audio(album)
    assert report["measured_track_count"] == 2
    tail = report["packet_tail"]
    assert tail["status"] == "video_packets_continue_after_audio"
    assert 3.5 < tail["packet_end_gap_seconds"] < 4.5
    assert tail["video_packets_starting_after_audio_end_count"] >= 7
    assert tail["last_video_packet_end_seconds"] > tail["last_audio_packet_end_seconds"]
    assert report["remote_publication_verified"] is False


def test_tail_inspection_remains_unknown_without_final_packet_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import probe_local_mp4_audio as mod

    monkeypatch.setattr(mod, "_tail_packets", lambda *a, **kw: [(0.0, 1.0)])
    tail = mod._inspect_packet_tail(
        tmp_path / "unopened.mp4", audio_start=0.0,
        audio_duration=40.0, video_duration=44.0,
    )
    assert tail["status"] == "unknown"
    assert tail["video_packets_starting_after_audio_end_count"] is None
    assert tail["packet_end_gap_seconds"] is None


def test_unavailable_video_and_invalid_window_fail_closed(tmp_path: Path) -> None:
    album = tmp_path / "album"
    album.mkdir()
    with pytest.raises(ValueError, match="invalid_window_settings"):
        probe_local_mp4_audio(album, window_seconds=100)
    if FFMPEG_PRESENT:
        with pytest.raises(ValueError, match="local_video_ambiguous_or_unavailable"):
            probe_local_mp4_audio(album)

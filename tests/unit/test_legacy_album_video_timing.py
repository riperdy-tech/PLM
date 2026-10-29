"""Tiny, local-only reproduction of the archived still-image video render timing."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image

from scripts.probe_local_mp4_audio import require_local_render_audio_sync
from scripts.produce_full_playlist import render_album_video

_HAS_MEDIA_TOOLS = all(shutil.which(x) for x in ("ffmpeg", "ffprobe"))


def _media_duration(path: Path) -> tuple[float, float, float]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,duration:format=duration", "-of", "json", str(path),
        ],
        check=True, capture_output=True, text=True, timeout=30,
    )
    data = json.loads(result.stdout)
    return (
        float(next(x["duration"] for x in data["streams"] if x["codec_type"] == "audio")),
        float(next(x["duration"] for x in data["streams"] if x["codec_type"] == "video")),
        float(data["format"]["duration"]),
    )


def _render(
    tmp_path: Path, *, exact_limit: bool, with_chapters: bool,
    video_tail_seconds: float = 0.0,
) -> tuple[float, float, float]:
    audio = tmp_path / "source.wav"
    artwork = tmp_path / "cover.png"
    mp4 = tmp_path / (
        ("bounded_" if exact_limit else "legacy_")
        + ("chapters" if with_chapters else "plain")
        + ("_tail.mp4" if video_tail_seconds else ".mp4")
    )
    duration = 4.0
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        waveform = bytearray()
        for k in range(64000):
            value = int(15000 * math.sin(k * 2 * math.pi * 220 / 16000))
            waveform.extend(value.to_bytes(2, "little", signed=True))
        wav.writeframes(waveform)
    Image.new("RGB", (64, 64), (80, 60, 90)).save(artwork)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-loop", "1", "-framerate", "2", "-i", str(artwork),
        "-i", str(audio),
    ]
    if with_chapters:
        chapters = tmp_path / "chapters_meta.txt"
        chapters.write_text(
            ";FFMETADATA1\ntitle=Local test\n[CHAPTER]\nTIMEBASE=1/1000\n"
            "START=0\nEND=4000\ntitle=One\n", encoding="utf-8",
        )
        cmd.extend(["-i", str(chapters)])
    cmd.extend([
        "-vf", "scale=64:64,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
        "-crf", "22", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
    ])
    if with_chapters:
        cmd.extend(["-map_metadata", "2"])
    if not video_tail_seconds:
        cmd.append("-shortest")
    cmd.extend(["-movflags", "+faststart"])
    if exact_limit or video_tail_seconds:
        cmd.extend(["-t", str(duration + video_tail_seconds)])
    cmd.append(str(mp4))
    subprocess.run(cmd, check=True, capture_output=True, timeout=75)
    return _media_duration(mp4)


@pytest.mark.skipif(not _HAS_MEDIA_TOOLS, reason="requires local FFmpeg")
def test_archived_still_image_shortest_vs_explicit_bound(tmp_path: Path) -> None:
    legacy_audio, legacy_video, legacy_container = _render(
        tmp_path, exact_limit=False, with_chapters=False,
    )
    chapter_audio, chapter_video, chapter_container = _render(
        tmp_path, exact_limit=False, with_chapters=True,
    )
    bounded_audio, bounded_video, bounded_container = _render(
        tmp_path, exact_limit=True, with_chapters=True,
    )
    print(f"legacy audio/video/container {legacy_audio}/{legacy_video}/{legacy_container}")
    print(f"chapters audio/video/container {chapter_audio}/{chapter_video}/{chapter_container}")
    print(f"bounded audio/video/container {bounded_audio}/{bounded_video}/{bounded_container}")
    assert 3.9 <= bounded_audio <= 4.2
    assert 3.9 <= bounded_video <= 4.5
    assert bounded_container <= 4.5
    # Historical reproduction may vary by installed FFmpeg/codec version:
    # retain descriptive observations rather than assuming a cause from a fixture.
    assert legacy_audio > 3.9
    assert legacy_video >= legacy_audio - 0.5
    assert 3.9 <= chapter_audio <= 4.2
    assert chapter_video >= chapter_audio - 0.5
    assert chapter_container >= chapter_audio - 0.5

    receipt = require_local_render_audio_sync(
        tmp_path / "bounded_chapters.mp4", expected_audio_seconds=4.0,
    )
    assert receipt["status"] == "locally_timing_consistent_only"
    assert receipt["remote_publication_verified"] is False
    assert receipt["rights_verified"] is False

    _render(
        tmp_path, exact_limit=False, with_chapters=False, video_tail_seconds=4.0,
    )
    with pytest.raises(
        ValueError, match="local_render_video_audio_duration_mismatch",
    ):
        require_local_render_audio_sync(
            tmp_path / "legacy_plain_tail.mp4", expected_audio_seconds=4.0,
        )


@pytest.mark.skipif(not _HAS_MEDIA_TOOLS, reason="requires local FFmpeg")
def test_archived_renderer_produces_guarded_short_mp4(tmp_path: Path) -> None:
    # Reuse temporary WAV and image generated above; do not invoke main(),
    # provider calls, any publishing endpoint, or touch stored user audio.
    _render(tmp_path, exact_limit=True, with_chapters=False)
    path = tmp_path / "archived_render.mp4"
    render_album_video(
        tmp_path / "source.wav", tmp_path / "cover.png", path,
    )
    result = require_local_render_audio_sync(path, expected_audio_seconds=4.0)
    assert result["status"] == "locally_timing_consistent_only"


def test_local_render_guard_rejects_unknown_final_packet_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import probe_local_mp4_audio as probe

    fixture = tmp_path / "synthetic.mp4"
    fixture.write_bytes(b"not an actual video; probe methods are stubbed")
    monkeypatch.setattr(
        probe, "_probe_streams",
        lambda _: (
            {"duration": "4.0", "start_time": "0.0"},
            {"duration": "4.0"}, 4.0,
        ),
    )
    monkeypatch.setattr(probe, "_inspect_packet_tail", lambda *a, **kw: {
        "status": "unknown", "packet_end_gap_seconds": None,
    })
    with pytest.raises(ValueError, match="local_render_packet_tail_not_clear"):
        require_local_render_audio_sync(fixture, expected_audio_seconds=4.0)
    with pytest.raises(ValueError, match="local_render_timing_inputs_invalid"):
        require_local_render_audio_sync(fixture, expected_audio_seconds=0.0)

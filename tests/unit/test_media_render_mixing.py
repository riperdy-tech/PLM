"""The FFmpeg audio master: exact segment lengths and linear loudness (review 2026-11-26,
P6 #26 and #27). Real FFmpeg on synthetic tones (``media``); skipped without the tools."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf  # type: ignore[import-untyped]
from media_factory.domain.enums import TransitionType
from media_factory.rendering.ffmpeg import FFmpegToolchain
from media_factory.rendering.profiles import RenderProfile
from media_factory.rendering.toolchain import AudioMasterSpec, AudioSegment

from tests.support.media_tools import require_media_tool

pytestmark = pytest.mark.media

PROFILE = RenderProfile(name="test_mix_v1", width=160, height=90, fps=10,
                        x264_preset="ultrafast", x264_crf=35)
RATE = 48000


@pytest.fixture(autouse=True)
def _tools() -> None:
    require_media_tool("ffmpeg")
    require_media_tool("ffprobe")


def _tone(path: Path, seconds: float, *, amplitude: float = 0.3,
          clicks: bool = False) -> Path:
    t = np.arange(int(seconds * RATE)) / RATE
    wave = amplitude * np.sin(2 * np.pi * 220.0 * t)
    # Some loudness range, as music has: FFmpeg's loudnorm treats a measured range of
    # 0 LU as unmeasured and never applies linear gain to it.
    wave[len(wave) // 2:] *= 0.5
    if clicks:  # full-scale spikes: no linear gain reaches the target under the ceiling
        wave[:: RATE * 2] = 0.99
    sf.write(path, np.stack([wave, wave], axis=1), RATE, subtype="PCM_24")
    return path


def test_a_source_a_little_short_of_its_manifest_keeps_the_timeline(tmp_path: Path) -> None:
    """``atrim`` only shortens: a source 50 ms short shortened the mix and post-render QA
    failed the package (``render_duration_mismatch``). It is padded to its length."""
    segments = tuple(AudioSegment(path=_tone(tmp_path / f"s{i}.wav", 1.95), duration_seconds=2.0)
                     for i in range(2))
    out = tmp_path / "master.wav"
    FFmpegToolchain().render_audio_master(
        AudioMasterSpec(segments=segments, transition=TransitionType.HARD_CUT,
                        transition_seconds=0.0), PROFILE, out)
    assert sf.info(out).duration == pytest.approx(4.0, abs=0.005)


def test_loudnorm_never_falls_back_to_dynamic_gain(tmp_path: Path) -> None:
    """``loudnorm linear=true`` silently switches to dynamic (pumping) gain when the
    linear gain would cross the true-peak ceiling. The master is then the linear gain
    with a peak limiter instead, and the report says which one was applied."""
    tool = FFmpegToolchain()
    quiet = AudioMasterSpec(
        segments=(AudioSegment(path=_tone(tmp_path / "spiky.wav", 6.0, amplitude=0.02,
                                          clicks=True), duration_seconds=6.0),),
        transition=TransitionType.HARD_CUT, transition_seconds=0.0)
    out = tmp_path / "spiky_master.wav"
    report = tool.render_audio_master(quiet, PROFILE, out)
    assert report["pass2"].get("normalization_type") != "linear"  # loudnorm gave up
    assert report["applied"] == "linear_gain_limiter"
    measured = tool.measure_loudness(out)
    assert measured.integrated_lufs == pytest.approx(PROFILE.loudness_target_lufs, abs=1.0)
    assert measured.true_peak_dbtp is not None and measured.true_peak_dbtp <= 0.0

    plain = AudioMasterSpec(
        segments=(AudioSegment(path=_tone(tmp_path / "plain.wav", 6.0, amplitude=0.1),
                               duration_seconds=6.0),),
        transition=TransitionType.HARD_CUT, transition_seconds=0.0)
    assert tool.render_audio_master(plain, PROFILE, tmp_path / "plain_master.wav")[
        "applied"] == "linear"

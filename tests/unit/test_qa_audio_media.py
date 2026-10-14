"""FFmpeg decode path of the P5 audio checks (R4). Marked ``media``.

These need ``ffmpeg`` and run in the dev image (FFmpeg 7) with
``PLM_REQUIRE_MEDIA_TOOLS=1``, so CI cannot pass them by skipping. They prove that a
non-WAV input is decoded by FFmpeg at its native rate and channel layout and then
measured and judged like a WAV, and that an undecodable non-WAV file blocks.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from plm_contracts.seams.c3 import DurationRangeSeconds
from quality_gate.audio.decode import DecodedAudio, decode_audio
from quality_gate.evaluators.technical import validate_technical_audio

from tests.support.audio_fixtures import clean_sine, write_wav
from tests.support.media_tools import require_media_tool

pytestmark = pytest.mark.media

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ANY_SHORT = DurationRangeSeconds(min_seconds=1, max_seconds=30)


def _encode(ffmpeg: str, source: Path, target: Path, *codec_args: str) -> Path:
    subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(source), *codec_args, str(target)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return target


def test_flac_decodes_bit_exactly_through_ffmpeg(tmp_path: Path) -> None:
    """Proves the FFmpeg path decodes a lossless FLAC to exactly the source PCM, keeping
    its rate and channels, and the evaluator measures it like the WAV."""
    ffmpeg = require_media_tool("ffmpeg")
    wav = write_wav(tmp_path / "src.wav", clean_sine(3.0))
    flac = _encode(ffmpeg, wav, tmp_path / "src.flac", "-c:a", "flac")

    from_wav = decode_audio(wav, timeout_seconds=60)
    from_flac = decode_audio(flac, timeout_seconds=60, ffmpeg_path=ffmpeg)
    assert isinstance(from_wav, DecodedAudio) and isinstance(from_flac, DecodedAudio)
    assert from_flac.decoder.startswith("ffmpeg ")
    assert (from_flac.sample_rate, from_flac.channels) == (44100, 2)
    np.testing.assert_array_equal(from_flac.samples, from_wav.samples)

    res = validate_technical_audio("eval_media", flac, ANY_SHORT, now=NOW)
    assert not res.has_blocking_defects, [f.message for f in res.findings]
    assert res.measurements is not None and res.measurements.container == "flac"
    wav_res = validate_technical_audio("eval_media", wav, ANY_SHORT, now=NOW)
    assert wav_res.measurements is not None
    decoder_keys = {"container", "codec", "decoder"}
    flac_values = {k: v for k, v in res.measurements.as_evidence().items() if k not in decoder_keys}
    wav_values = {
        k: v for k, v in wav_res.measurements.as_evidence().items() if k not in decoder_keys
    }
    assert flac_values == wav_values


def test_mp3_decodes_through_ffmpeg_and_measures_close_to_its_source(tmp_path: Path) -> None:
    """Proves a lossy MP3 is decoded and measured: native rate and channels, duration
    within MP3 framing slack, loudness within 0.3 LU of the -14 LUFS source (a lossy
    codec may shift the level slightly: LAME at 192 kbit/s CBR read 0.26 LU low on this
    tone, VBR V0 0.00 LU), no blocking finding; and decoding the same bytes twice gives
    identical samples."""
    ffmpeg = require_media_tool("ffmpeg")
    wav = write_wav(tmp_path / "src.wav", clean_sine(3.0))
    mp3 = _encode(ffmpeg, wav, tmp_path / "src.mp3", "-c:a", "libmp3lame", "-q:a", "0")

    first = decode_audio(mp3, timeout_seconds=60, ffmpeg_path=ffmpeg)
    second = decode_audio(mp3, timeout_seconds=60, ffmpeg_path=ffmpeg)
    assert isinstance(first, DecodedAudio) and isinstance(second, DecodedAudio)
    assert (first.sample_rate, first.channels) == (44100, 2)
    np.testing.assert_array_equal(first.samples, second.samples)
    assert abs(first.duration_seconds - 3.0) < 0.06

    res = validate_technical_audio("eval_media", mp3, ANY_SHORT, now=NOW)
    assert not res.has_blocking_defects, [f.message for f in res.findings]
    lufs = next(c for c in res.checks if c.check_type == "integrated_loudness")
    assert lufs.measured_value == pytest.approx(-14.0, abs=0.3)
    assert res.measurements is not None and res.measurements.container == "mp3"


def test_corrupt_non_wav_is_a_blocking_ffmpeg_decode_failure(tmp_path: Path) -> None:
    """Proves bytes FFmpeg cannot decode are a blocking DECODE_FAILED, never a pass."""
    ffmpeg = require_media_tool("ffmpeg")
    path = tmp_path / "corrupt.mp3"
    path.write_bytes(b"\x00\x01garbage-not-audio" * 200)
    res = validate_technical_audio("eval_media", path, ANY_SHORT, now=NOW, ffmpeg_path=ffmpeg)
    assert [(f.finding_code, f.blocking) for f in res.findings] == [("DECODE_FAILED", True)]

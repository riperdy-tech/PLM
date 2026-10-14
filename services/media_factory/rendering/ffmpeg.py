"""The FFmpeg 7 toolchain (P6 §23, §24, §30, R4).

Every call is an explicit argv list run without a shell; file names never reach a
command string. The steps:

* **Audio master.** Each source is resampled to the profile's rate and layout and
  trimmed to its timeline duration; consecutive sources are joined by the declared
  transition (``acrossfade`` with triangular curves for a crossfade, ``concat`` for a
  hard cut, ``anullsrc`` + ``concat`` for a silence gap). The mix is normalised with
  two-pass ``loudnorm`` to the profile's integrated loudness, true peak and loudness
  range (pass 1 measures, pass 2 applies the measured values in linear mode), then
  resampled back to the profile's rate and written as 24-bit PCM WAV.
* **Video master.** A procedural visualiser: ``showwaves`` (``cline`` mode, square-root
  scale) drawn from the audio master in the brand's accent colour and overlaid on the
  brand background image, encoded with ``libx264`` (profile preset and CRF, ``yuv420p``)
  plus ``aac`` audio into MP4 with ``+faststart``. Metadata is stripped and bit-exact
  flags are set so the bytes do not carry encoder timestamps.
* **Probe.** ``ffprobe -show_format -show_streams`` as JSON.
* **Loudness.** The ``ebur128`` meter (BS.1770, true peak on) over the produced file's
  audio: a separate meter from the ``loudnorm`` normaliser, run on what was produced.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from ..domain.enums import TransitionType
from ..errors import MediaInfrastructureError
from .profiles import RenderProfile
from .toolchain import (
    AudioMasterSpec,
    LoudnessMeasurement,
    MediaToolError,
    ProbeReport,
    StreamInfo,
    VisualizerSpec,
)

__all__ = ["TOOLCHAIN_UNAVAILABLE", "FFmpegToolchain"]

TOOLCHAIN_UNAVAILABLE: Final[str] = "render_toolchain_unavailable"
_STDERR_TAIL: Final[int] = 2000
_HEX_COLOUR: Final = re.compile(r"\A#[0-9A-Fa-f]{6}\Z")
_INTEGRATED: Final = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(\S+)\s*LUFS")
_TRUE_PEAK: Final = re.compile(r"True peak:\s*\n\s*Peak:\s*(\S+)\s*dBFS")


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _rate(text: str | None) -> float | None:
    if not text or text in ("0/0", "0"):
        return None
    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return None


class FFmpegToolchain:
    """:class:`~.toolchain.MediaToolchain` over the ``ffmpeg`` and ``ffprobe`` binaries."""

    def __init__(self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                 timeout_seconds: float = 3600.0) -> None:
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._timeout = timeout_seconds
        self._version: str | None = None

    # -- process plumbing ------------------------------------------------------------------

    def _resolve(self, name: str) -> str:
        path = shutil.which(name)
        if path is None:
            raise MediaInfrastructureError(
                TOOLCHAIN_UNAVAILABLE, f"{name} is not on PATH (the dev image provides FFmpeg 7)")
        return path

    def _run(self, tool: str, args: Sequence[str], *, step: str) -> subprocess.CompletedProcess[str]:
        argv = [self._resolve(tool), *args]
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self._timeout, check=False, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise MediaToolError("render_timeout", f"{step} exceeded {self._timeout}s") from exc
        if done.returncode != 0:
            raise MediaToolError(
                "render_failed", f"{step} exited {done.returncode}: {done.stderr[-_STDERR_TAIL:]}")
        return done

    @property
    def version(self) -> str:
        if self._version is None:
            first = self._run(self._ffmpeg, ["-hide_banner", "-version"], step="version")
            self._version = first.stdout.splitlines()[0].strip() if first.stdout else "ffmpeg"
        return self._version

    # -- audio master ----------------------------------------------------------------------

    @staticmethod
    def _mix_graph(spec: AudioMasterSpec, profile: RenderProfile) -> tuple[list[str], str]:
        if not spec.segments:
            raise MediaToolError("render_failed", "an audio master needs at least one segment")
        inputs: list[str] = []
        parts: list[str] = []
        layout = "stereo" if profile.channels == 2 else "mono"
        for i, segment in enumerate(spec.segments):
            inputs += ["-i", str(segment.path)]
            parts.append(
                f"[{i}:a]aresample={profile.sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts={layout},"
                f"atrim=end={segment.duration_seconds:.6f},asetpts=PTS-STARTPTS[s{i}]")
        current = "s0"
        d = spec.transition_seconds
        for i in range(1, len(spec.segments)):
            label = f"m{i}"
            if spec.transition in (TransitionType.SHORT_CROSSFADE, TransitionType.LONG_CROSSFADE):
                parts.append(f"[{current}][s{i}]acrossfade=d={d:.6f}:c1=tri:c2=tri[{label}]")
            elif spec.transition is TransitionType.SILENCE_GAP:
                parts.append(f"anullsrc=r={profile.sample_rate}:cl={layout},"
                             f"atrim=end={d:.6f},aformat=sample_fmts=fltp[g{i}]")
                parts.append(f"[{current}][g{i}][s{i}]concat=n=3:v=0:a=1[{label}]")
            else:
                parts.append(f"[{current}][s{i}]concat=n=2:v=0:a=1[{label}]")
            current = label
        parts.append(f"[{current}]anull[mix]")
        return inputs, ";".join(parts)

    def render_audio_master(self, spec: AudioMasterSpec, profile: RenderProfile,
                            out: Path) -> dict[str, Any]:
        inputs, graph = self._mix_graph(spec, profile)
        target = (f"I={profile.loudness_target_lufs}:TP={profile.true_peak_dbtp}:"
                  f"LRA={profile.loudness_range_lu}")
        first = self._run(self._ffmpeg, [
            "-hide_banner", "-nostats", "-nostdin", *inputs,
            "-filter_complex", f"{graph};[mix]loudnorm={target}:print_format=json[out]",
            "-map", "[out]", "-f", "null", "-",
        ], step="loudnorm pass 1")
        measured = self._loudnorm_json(first.stderr)
        values = {k: measured.get(k) for k in
                  ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
        if any(_number(str(v)) is None for v in values.values()):
            raise MediaToolError("loudness_unmeasurable",
                                 f"loudnorm could not measure the mix: {values}")
        second = self._run(self._ffmpeg, [
            "-hide_banner", "-nostats", "-nostdin", "-y", *inputs,
            "-filter_complex",
            (f"{graph};[mix]loudnorm={target}:measured_I={values['input_i']}:"
             f"measured_TP={values['input_tp']}:measured_LRA={values['input_lra']}:"
             f"measured_thresh={values['input_thresh']}:offset={values['target_offset']}:"
             f"linear=true:print_format=json,aresample={profile.sample_rate}[out]"),
            "-map", "[out]", "-c:a", "pcm_s24le", "-ar", str(profile.sample_rate),
            "-ac", str(profile.channels), "-map_metadata", "-1", "-fflags", "+bitexact",
            "-flags:a", "+bitexact", str(out),
        ], step="loudnorm pass 2")
        applied = self._loudnorm_json(second.stderr)
        return {"filter": "loudnorm", "target": target, "pass1": measured, "pass2": applied}

    @staticmethod
    def _loudnorm_json(stderr: str) -> dict[str, Any]:
        start = stderr.rfind("{")
        end = stderr.rfind("}")
        if start < 0 or end < start:
            raise MediaToolError("loudness_unmeasurable", "loudnorm printed no measurement")
        try:
            parsed = json.loads(stderr[start:end + 1])
        except json.JSONDecodeError as exc:
            raise MediaToolError("loudness_unmeasurable", f"unreadable loudnorm output: {exc}") from exc
        if not isinstance(parsed, dict):
            raise MediaToolError("loudness_unmeasurable", "loudnorm output is not an object")
        return parsed

    # -- video master ----------------------------------------------------------------------

    def render_video_master(self, audio_master: Path, visual: VisualizerSpec,
                            profile: RenderProfile, out: Path) -> None:
        if _HEX_COLOUR.match(visual.wave_color) is None:
            raise MediaToolError("render_failed", f"not a #RRGGBB colour: {visual.wave_color!r}")
        wave_height = max(2, int(profile.height * profile.waveform_height_ratio) // 2 * 2)
        colour = "0x" + visual.wave_color[1:]
        graph = (
            f"[1:a]showwaves=s={profile.width}x{wave_height}:mode=cline:rate={profile.fps}:"
            f"colors={colour}:scale=sqrt,format=rgba[w];"
            f"[0:v]scale={profile.width}:{profile.height},setsar=1[bg];"
            f"[bg][w]overlay=0:(H-h)/2:shortest=1,format={profile.pixel_format}[v]"
        )
        self._run(self._ffmpeg, [
            "-hide_banner", "-nostats", "-nostdin", "-y",
            "-loop", "1", "-framerate", str(profile.fps), "-i", str(visual.background_png),
            "-i", str(audio_master),
            "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", profile.x264_preset, "-crf", str(profile.x264_crf),
            "-pix_fmt", profile.pixel_format, "-r", str(profile.fps),
            "-c:a", "aac", "-b:a", profile.audio_bitrate, "-ar", str(profile.sample_rate),
            "-ac", str(profile.channels), "-shortest", "-movflags", "+faststart",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
            "-flags:a", "+bitexact", "-f", profile.container, str(out),
        ], step="video encode")

    # -- measurement -----------------------------------------------------------------------

    def probe(self, path: Path) -> ProbeReport:
        done = self._run(self._ffprobe, [
            "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path),
        ], step="ffprobe")
        try:
            data = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            raise MediaToolError("container_unreadable", f"ffprobe output: {exc}") from exc
        fmt = data.get("format") or {}
        streams: list[StreamInfo] = []
        for raw in data.get("streams") or []:
            codec_type = str(raw.get("codec_type", ""))
            streams.append(StreamInfo(
                codec_type=codec_type,
                codec_name=str(raw.get("codec_name", "")),
                width=raw.get("width"),
                height=raw.get("height"),
                fps=_rate(raw.get("avg_frame_rate")) if codec_type == "video" else None,
                pixel_format=raw.get("pix_fmt"),
                sample_rate=int(raw["sample_rate"]) if raw.get("sample_rate") else None,
                channels=raw.get("channels"),
            ))
        duration = _number(str(fmt.get("duration", "")))
        return ProbeReport(
            format_name=str(fmt.get("format_name", "")),
            duration_seconds=duration,
            size_bytes=int(fmt.get("size") or path.stat().st_size),
            streams=tuple(streams),
        )

    def measure_loudness(self, path: Path) -> LoudnessMeasurement:
        done = self._run(self._ffmpeg, [
            "-hide_banner", "-nostats", "-nostdin", "-i", str(path), "-map", "0:a:0",
            "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-",
        ], step="ebur128")
        integrated = _INTEGRATED.findall(done.stderr)
        peak = _TRUE_PEAK.findall(done.stderr)
        return LoudnessMeasurement(
            integrated_lufs=_number(integrated[-1]) if integrated else None,
            true_peak_dbtp=_number(peak[-1]) if peak else None,
            meter="ffmpeg ebur128 (BS.1770-4, true peak)",
        )

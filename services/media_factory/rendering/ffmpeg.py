"""The FFmpeg 7 toolchain (P6 §23, §24, §30, R4).

Every call is an explicit argv list run without a shell; file names never reach a
command string. The steps:

* **Audio master.** Each source is resampled to the profile's rate and layout and
  trimmed to its timeline duration; consecutive sources are joined by the declared
  transition (``acrossfade`` with triangular curves for a crossfade, ``concat`` for a
  hard cut, ``anullsrc`` + ``concat`` for a silence gap). The mix is normalised with
  two-pass ``loudnorm`` to the profile's integrated loudness, true peak and loudness
  range (pass 1 measures, pass 2 applies the measured values in linear mode), then
  resampled back to the profile's rate and written as 24-bit PCM WAV. Each source is
  padded to its timeline length before the trim, so a source a few ms short cannot
  shorten the mix. When ``loudnorm`` falls back to dynamic gain (the linear gain would
  cross the true-peak ceiling, or the range is unmeasured), a third pass applies the
  linear gain with a peak limiter at the ceiling instead; the report's ``applied`` says
  which (``linear`` or ``linear_gain_limiter``).
* **Still video** (the only video master; no waveform or motion, owner 2026-11-04). The
  brand background, later the owner's thumbnail art, scaled to cover the frame (once a second, then
  repeated to the frame rate), centre-cropped and held still (``-tune stillimage``) for exactly the audio master's length, converted to
  TV-range BT.709 (a JPEG decodes as full-range ``yuvj420p`` otherwise), same codecs.
* **Probe.** ``ffprobe -show_format -show_streams`` as JSON.
* **Loudness.** The ``ebur128`` meter (BS.1770, true peak on) over the produced file's
  audio: a separate meter from the ``loudnorm`` normaliser, run on what was produced.

Each step's timeout is ``timeout_seconds`` or 2.5 times the programme length it
processes, whichever is longer, so a 120-minute render (ADR-0044) is not cut off by a
limit sized for short ones.
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
)

__all__ = ["TOOLCHAIN_UNAVAILABLE", "FFmpegToolchain"]

TOOLCHAIN_UNAVAILABLE: Final[str] = "render_toolchain_unavailable"
_STDERR_TAIL: Final[int] = 2000
_TIMEOUT_PER_PROGRAMME_SECOND: Final[float] = 2.5
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

    def _timeout_for(self, programme_seconds: float | None) -> float:
        """The step's timeout for a programme of ``programme_seconds`` (unknown: the base)."""
        if programme_seconds is None:
            return self._timeout
        return max(self._timeout, _TIMEOUT_PER_PROGRAMME_SECOND * programme_seconds)

    def _run(self, tool: str, args: Sequence[str], *, step: str,
             programme_seconds: float | None = None) -> subprocess.CompletedProcess[str]:
        argv = [self._resolve(tool), *args]
        timeout = self._timeout_for(programme_seconds)
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise MediaToolError("render_timeout", f"{step} exceeded {timeout}s") from exc
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
                # Padded, then trimmed, to exactly its timeline length: a source a few ms
                # shorter than its manifest must not shorten the mix (QA would fail it).
                f"apad=whole_dur={segment.duration_seconds:.6f},"
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
        programme = sum(segment.duration_seconds for segment in spec.segments)
        target = (f"I={profile.loudness_target_lufs}:TP={profile.true_peak_dbtp}:"
                  f"LRA={profile.loudness_range_lu}")
        first = self._run(self._ffmpeg, [
            "-hide_banner", "-nostats", "-nostdin", *inputs,
            "-filter_complex", f"{graph};[mix]loudnorm={target}:print_format=json[out]",
            "-map", "[out]", "-f", "null", "-",
        ], step="loudnorm pass 1", programme_seconds=programme)
        measured = self._loudnorm_json(first.stderr)
        values = {k: measured.get(k) for k in
                  ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
        if any(_number(str(v)) is None for v in values.values()):
            raise MediaToolError("loudness_unmeasurable",
                                 f"loudnorm could not measure the mix: {values}")
        output = ["-map", "[out]", "-c:a", "pcm_s24le", "-ar", str(profile.sample_rate),
                  "-ac", str(profile.channels), "-map_metadata", "-1", "-fflags", "+bitexact",
                  "-flags:a", "+bitexact", str(out)]
        second = self._run(self._ffmpeg, [
            "-hide_banner", "-nostats", "-nostdin", "-y", *inputs,
            "-filter_complex",
            (f"{graph};[mix]loudnorm={target}:measured_I={values['input_i']}:"
             f"measured_TP={values['input_tp']}:measured_LRA={values['input_lra']}:"
             f"measured_thresh={values['input_thresh']}:offset={values['target_offset']}:"
             f"linear=true:print_format=json,aresample={profile.sample_rate}[out]"),
            *output,
        ], step="loudnorm pass 2", programme_seconds=programme)
        applied = self._loudnorm_json(second.stderr)
        if applied.get("normalization_type") == "linear":
            return {"filter": "loudnorm", "target": target, "pass1": measured,
                    "pass2": applied, "applied": "linear"}
        # loudnorm silently switches to dynamic (pumping) gain when the linear gain would
        # cross the true-peak ceiling. Apply that linear gain anyway and let a peak
        # limiter hold the ceiling (the encoder's overshoot is covered by the profile's
        # true-peak tolerance), so the music keeps its own dynamics.
        gain_db = profile.loudness_target_lufs - float(str(values["input_i"]))
        ceiling = 10 ** (profile.true_peak_dbtp / 20)
        self._run(self._ffmpeg, [
            "-hide_banner", "-nostats", "-nostdin", "-y", *inputs,
            "-filter_complex",
            (f"{graph};[mix]volume={gain_db:.3f}dB,"
             f"aresample=192000,alimiter=limit={ceiling:.6f}:attack=1:release=20:level=false,"
             f"aresample={profile.sample_rate}[out]"),
            *output,
        ], step="linear gain and limiter", programme_seconds=programme)
        return {"filter": "loudnorm", "target": target, "pass1": measured, "pass2": applied,
                "applied": "linear_gain_limiter", "gain_db": round(gain_db, 3),
                "limiter_ceiling_dbtp": profile.true_peak_dbtp}

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

    def render_still_video(self, audio_master: Path, image: Path, profile: RenderProfile,
                           out: Path) -> None:
        programme = self.probe(audio_master).duration_seconds
        if programme is None or programme <= 0:
            raise MediaToolError("render_failed", "the audio master has no measurable length")
        graph = (
            f"[0:v]scale={profile.width}:{profile.height}:force_original_aspect_ratio=increase:"
            f"flags=lanczos:out_range=tv:out_color_matrix=bt709,"
            f"crop={profile.width}:{profile.height},setsar=1,format={profile.pixel_format},"
            f"fps={profile.fps}[v]"
        )
        # The picture is read at one frame per second and scaled once per second, then
        # repeated up to the profile's frame rate: scaling a large owner image for every
        # output frame made a 24-minute video take about 40 minutes (measured 2026-11-06:
        # 103 s vs 9 s per minute of video, byte-identical output).
        self._run(self._ffmpeg, [
            "-hide_banner", "-nostats", "-nostdin", "-y",
            "-loop", "1", "-framerate", "1", "-i", str(image),
            "-i", str(audio_master),
            "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", profile.x264_preset, "-tune", "stillimage",
            "-crf", str(profile.x264_crf), "-pix_fmt", profile.pixel_format,
            "-r", str(profile.fps), "-color_range", "tv", "-colorspace", "bt709",
            "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", profile.audio_bitrate, "-ar", str(profile.sample_rate),
            "-ac", str(profile.channels), "-t", f"{programme:.3f}",
            "-movflags", "+faststart",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
            "-f", profile.container, str(out),
        ], step="still video encode", programme_seconds=programme)

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
                duration_seconds=_number(str(raw.get("duration", ""))),
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
        ], step="ebur128", programme_seconds=self.probe(path).duration_seconds)
        integrated = _INTEGRATED.findall(done.stderr)
        peak = _TRUE_PEAK.findall(done.stderr)
        return LoudnessMeasurement(
            integrated_lufs=_number(integrated[-1]) if integrated else None,
            true_peak_dbtp=_number(peak[-1]) if peak else None,
            meter="ffmpeg ebur128 (BS.1770-4, true peak)",
        )

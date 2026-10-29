"""Post-render QA of the produced video master (P6 §30, R4).

Judges what ffprobe and the loudness meter report about the *produced file* against
the render profile and the timeline. Every failed check adds a reason code; any code
blocks the package (status ``failed``). Nothing here trusts the encoder's or the
normaliser's own account of what it did.

Reason codes:

``empty_output``, ``container_unreadable``
    no bytes, or ffprobe could not read the container;
``container_mismatch``
    not the profile's container;
``video_stream_missing``, ``audio_stream_missing``, ``unexpected_stream_count``
    exactly one video and one audio stream are required;
``video_codec_mismatch``, ``resolution_mismatch``, ``fps_mismatch``, ``pixel_format_mismatch``
    the video stream against the profile;
``audio_codec_mismatch``, ``audio_sample_rate_mismatch``, ``audio_channels_mismatch``
    the audio stream against the profile;
``duration_unmeasured``, ``render_duration_mismatch``
    a finite positive container duration against the timeline, within tolerance;
``expected_duration_unmeasured``, ``duration_window_invalid``
    reject invalid/nonfinite timeline and requested duration bounds;
``stream_duration_unmeasured``, ``stream_duration_mismatch``,
``video_audio_duration_mismatch``
    the independently reported audio/video stream durations against the
    expected timeline and each other, rather than accepting only container time;
``duration_outside_required_window``
    the container duration against the declared playlist range;
``loudness_unmeasurable``, ``loudness_out_of_tolerance``,
``true_peak_unmeasurable``, ``true_peak_exceeded``
    the finite measured integrated loudness against the profile's target and tolerance;
    a missing/nonfinite true-peak measurement is never treated as a safe peak.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .profiles import RenderProfile
from .toolchain import LoudnessMeasurement, ProbeReport

__all__ = ["PostRenderCheckResult", "post_render_qa"]

_FPS_TOLERANCE = 0.01


@dataclass(frozen=True)
class PostRenderCheckResult:
    """Outcome of post-render QA: ``reason_codes`` is empty only when every check held."""

    passed: bool
    reason_codes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def post_render_qa(
    *,
    probe: ProbeReport | None,
    loudness: LoudnessMeasurement | None,
    profile: RenderProfile,
    expected_duration_seconds: float,
    required_window_seconds: tuple[float, float],
    size_bytes: int,
) -> PostRenderCheckResult:
    """Every check against the produced file; ``probe=None`` means ffprobe failed."""
    failures: list[tuple[str, str]] = []

    def fail(code: str, detail: str) -> None:
        failures.append((code, detail))

    if size_bytes <= 0:
        fail("empty_output", "the video master has no bytes")
    if probe is None:
        fail("container_unreadable", "ffprobe could not read the video master")
        return _result(failures, probe, loudness, profile)

    if profile.container not in probe.format_name.split(","):
        fail("container_mismatch", f"format {probe.format_name!r}, profile {profile.container!r}")
    videos = [s for s in probe.streams if s.codec_type == "video"]
    audios = [s for s in probe.streams if s.codec_type == "audio"]
    if not videos:
        fail("video_stream_missing", "no video stream")
    if not audios:
        fail("audio_stream_missing", "no audio stream")
    if len(videos) > 1 or len(audios) > 1 or len(probe.streams) != len(videos) + len(audios):
        fail("unexpected_stream_count", f"{len(probe.streams)} streams "
             f"({len(videos)} video, {len(audios)} audio)")
    if videos:
        v = videos[0]
        if v.codec_name != profile.video_codec:
            fail("video_codec_mismatch", f"{v.codec_name!r}, profile {profile.video_codec!r}")
        if (v.width, v.height) != (profile.width, profile.height):
            fail("resolution_mismatch",
                 f"{v.width}x{v.height}, profile {profile.width}x{profile.height}")
        if v.fps is None or not math.isfinite(v.fps) or v.fps <= 0 or abs(v.fps - profile.fps) > _FPS_TOLERANCE:
            fail("fps_mismatch", f"{v.fps}, profile {profile.fps}")
        if v.pixel_format != profile.pixel_format:
            fail("pixel_format_mismatch", f"{v.pixel_format!r}, profile {profile.pixel_format!r}")
    if audios:
        a = audios[0]
        if a.codec_name != profile.audio_codec:
            fail("audio_codec_mismatch", f"{a.codec_name!r}, profile {profile.audio_codec!r}")
        if a.sample_rate != profile.sample_rate:
            fail("audio_sample_rate_mismatch", f"{a.sample_rate}, profile {profile.sample_rate}")
        if a.channels != profile.channels:
            fail("audio_channels_mismatch", f"{a.channels}, profile {profile.channels}")

    if not math.isfinite(expected_duration_seconds) or expected_duration_seconds <= 0:
        fail("expected_duration_unmeasured", "timeline has no finite positive duration")
    low, high = required_window_seconds
    if (not math.isfinite(low) or not math.isfinite(high)
            or low <= 0 or high < low):
        fail("duration_window_invalid", "playlist duration window is not finite and valid")

    # The container's duration can equal the expected playlist while one
    # elementary stream ends early. Require stream-level FFprobe evidence too.
    measured_streams: dict[str, float] = {}
    for kind, streams in (("video", videos), ("audio", audios)):
        if not streams:
            continue  # The missing-stream reason code is already recorded.
        measured = streams[0].duration_seconds
        if measured is None or not math.isfinite(measured) or measured <= 0:
            fail("stream_duration_unmeasured", f"{kind} stream has no valid duration")
            continue
        measured_streams[kind] = measured
        if abs(measured - expected_duration_seconds) > profile.duration_tolerance_seconds:
            fail("stream_duration_mismatch",
                 f"{kind} stream {measured:.3f}s, timeline "
                 f"{expected_duration_seconds:.3f}s")
    if {"video", "audio"} <= measured_streams.keys() and abs(
        measured_streams["video"] - measured_streams["audio"]
    ) > profile.duration_tolerance_seconds:
        fail("video_audio_duration_mismatch",
             f"video {measured_streams['video']:.3f}s versus "
             f"audio {measured_streams['audio']:.3f}s")

    duration = probe.duration_seconds
    if duration is None or not math.isfinite(duration) or duration <= 0:
        fail("duration_unmeasured", "the container has no finite positive duration")
    else:
        if abs(duration - expected_duration_seconds) > profile.duration_tolerance_seconds:
            fail("render_duration_mismatch",
                 f"{duration:.3f}s produced, timeline {expected_duration_seconds:.3f}s "
                 f"(tolerance {profile.duration_tolerance_seconds}s)")
        low, high = required_window_seconds
        if not low - profile.duration_tolerance_seconds <= duration <= \
                high + profile.duration_tolerance_seconds:
            fail("duration_outside_required_window",
                 f"{duration:.3f}s outside [{low:.3f}s, {high:.3f}s]")

    integrated = loudness.integrated_lufs if loudness is not None else None
    if integrated is None or not math.isfinite(integrated):
        fail("loudness_unmeasurable", "the meter reported no finite integrated loudness")
    else:
        deviation = integrated - profile.loudness_target_lufs
        if abs(deviation) > profile.loudness_tolerance_lu:
            fail("loudness_out_of_tolerance",
                 f"{integrated:.2f} LUFS, target {profile.loudness_target_lufs} "
                 f"± {profile.loudness_tolerance_lu} LU")
    peak = loudness.true_peak_dbtp if loudness is not None else None
    if peak is None or not math.isfinite(peak):
        fail("true_peak_unmeasurable", "the meter reported no finite true peak")
    else:
        ceiling = profile.true_peak_dbtp + profile.true_peak_tolerance_db
        if peak > ceiling:
            fail("true_peak_exceeded",
                 f"{peak:.2f} dBTP above the {ceiling:.2f} dBTP ceiling")
    return _result(failures, probe, loudness, profile)


def _json_safe_metric(value: Any) -> Any:
    """Replace nonfinite measurements with unknown, never persisted JSON NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe_metric(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_metric(child) for child in value]
    return value


def _result(failures: list[tuple[str, str]], probe: ProbeReport | None,
            loudness: LoudnessMeasurement | None,
            profile: RenderProfile) -> PostRenderCheckResult:
    metrics: dict[str, Any] = {
        "render_profile": profile.name,
        "probe": None if probe is None else probe.to_dict(),
        "loudness": None if loudness is None else {
            "integrated_lufs": loudness.integrated_lufs,
            "true_peak_dbtp": loudness.true_peak_dbtp,
            "meter": loudness.meter,
        },
    }
    codes = list(dict.fromkeys(code for code, _ in failures))
    return PostRenderCheckResult(
        passed=not failures,
        reason_codes=codes,
        reasons=[f"{code}: {detail}" for code, detail in failures],
        metrics=_json_safe_metric(metrics),
    )

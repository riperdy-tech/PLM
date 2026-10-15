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
    the container duration against the timeline, within the profile's tolerance;
``duration_outside_required_window``
    the container duration against the declared playlist range;
``loudness_unmeasurable``, ``loudness_out_of_tolerance``, ``true_peak_exceeded``
    the measured integrated loudness against the profile's target and tolerance, and
    the measured true peak against its ceiling plus tolerance.
"""

from __future__ import annotations

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
        if v.fps is None or abs(v.fps - profile.fps) > _FPS_TOLERANCE:
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

    duration = probe.duration_seconds
    if duration is None:
        fail("duration_unmeasured", "the container reports no duration")
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

    if loudness is None or loudness.integrated_lufs is None:
        fail("loudness_unmeasurable", "the meter reported no integrated loudness")
    else:
        deviation = loudness.integrated_lufs - profile.loudness_target_lufs
        if abs(deviation) > profile.loudness_tolerance_lu:
            fail("loudness_out_of_tolerance",
                 f"{loudness.integrated_lufs:.2f} LUFS, target {profile.loudness_target_lufs} "
                 f"± {profile.loudness_tolerance_lu} LU")
        ceiling = profile.true_peak_dbtp + profile.true_peak_tolerance_db
        if loudness.true_peak_dbtp is not None and loudness.true_peak_dbtp > ceiling:
            fail("true_peak_exceeded",
                 f"{loudness.true_peak_dbtp:.2f} dBTP above the {ceiling:.2f} dBTP ceiling")
    return _result(failures, probe, loudness, profile)


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
        metrics=metrics,
    )

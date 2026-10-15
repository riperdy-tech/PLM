"""Named, versioned render profiles (P6 §22).

A job names its profile; a worker never substitutes its own defaults. Each profile
declares the container, codecs, resolution, frame rate, encoder quality, and the
loudness target the audio master is normalised to (P6 §24: normalisation happens only
because the profile declares it). The tolerances are what post-render QA
(:mod:`.qa`) holds the produced file to.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from ..errors import MediaAssemblyError

__all__ = ["RENDER_PROFILES", "RenderProfile", "render_profile"]


@dataclass(frozen=True, slots=True)
class RenderProfile:
    name: str
    width: int
    height: int
    fps: int
    video_codec: str = "h264"
    """ffprobe ``codec_name`` of the video stream; encoded with ``libx264``."""
    pixel_format: str = "yuv420p"
    x264_preset: str = "veryfast"
    x264_crf: int = 20
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    sample_rate: int = 48000
    channels: int = 2
    container: str = "mp4"
    loudness_target_lufs: float = -14.0
    loudness_tolerance_lu: float = 1.0
    true_peak_dbtp: float = -1.0
    true_peak_tolerance_db: float = 1.0
    """Lossy (AAC) encoding may overshoot the limiter's ceiling by up to this much."""
    loudness_range_lu: float = 11.0
    duration_tolerance_seconds: float = 0.5
    """Produced duration vs. the timeline: encoder priming and frame rounding only."""
    waveform_height_ratio: float = 0.4

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


RENDER_PROFILES: Final = MappingProxyType({
    profile.name: profile
    for profile in (
        RenderProfile(name="yt_longform_1080p_v1", width=1920, height=1080, fps=30),
        RenderProfile(name="preview_360p_v1", width=640, height=360, fps=15,
                      x264_preset="ultrafast", x264_crf=30, audio_bitrate="128k"),
    )
})
"""Every profile a request may name."""


def render_profile(name: str) -> RenderProfile:
    """The named profile, or a refusal (``unknown_render_profile``)."""
    try:
        return RENDER_PROFILES[name]
    except KeyError as exc:
        raise MediaAssemblyError(
            "unknown_render_profile",
            f"render profile {name!r} is not declared; known: {sorted(RENDER_PROFILES)}",
            ["render_profile"],
        ) from exc

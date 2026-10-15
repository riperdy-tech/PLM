"""What the renderer asks of a media toolchain, and what it gets back (P6 §23, §30).

:class:`MediaToolchain` is the seam between P6's render orchestration and the tool that
encodes. The production implementation is :class:`~.ffmpeg.FFmpegToolchain` (FFmpeg 7
and ffprobe, called with explicit argv). Tests on a host without FFmpeg use a stand-in
from ``tests/support`` that proves the wiring and nothing about encoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..domain.enums import TransitionType
from .profiles import RenderProfile

__all__ = [
    "AudioMasterSpec",
    "AudioSegment",
    "LoudnessMeasurement",
    "MediaToolError",
    "MediaToolchain",
    "ProbeReport",
    "StreamInfo",
    "VisualizerSpec",
]


class MediaToolError(RuntimeError):
    """The tool ran and failed (bad input, encoder error); ``code`` is the reason."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class AudioSegment:
    path: Path
    duration_seconds: float
    """The timeline duration (P5's measured duration); the source is trimmed to it."""


@dataclass(frozen=True, slots=True)
class AudioMasterSpec:
    segments: tuple[AudioSegment, ...]
    transition: TransitionType
    transition_seconds: float


@dataclass(frozen=True, slots=True)
class VisualizerSpec:
    background_png: Path
    wave_color: str
    """``#RRGGBB``."""


@dataclass(frozen=True, slots=True)
class StreamInfo:
    codec_type: str
    codec_name: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    pixel_format: str | None = None
    sample_rate: int | None = None
    channels: int | None = None


@dataclass(frozen=True, slots=True)
class ProbeReport:
    format_name: str
    duration_seconds: float | None
    size_bytes: int
    streams: tuple[StreamInfo, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_name": self.format_name,
            "duration_seconds": self.duration_seconds,
            "size_bytes": self.size_bytes,
            "streams": [
                {k: getattr(s, k) for k in s.__dataclass_fields__} for s in self.streams
            ],
        }


@dataclass(frozen=True, slots=True)
class LoudnessMeasurement:
    integrated_lufs: float | None
    """``None`` when the meter reports no measurable programme (silence, ``-inf``)."""
    true_peak_dbtp: float | None
    meter: str


class MediaToolchain(Protocol):
    @property
    def version(self) -> str:
        """The tool and version that produced the output, for the render report."""
        ...

    def render_audio_master(self, spec: AudioMasterSpec, profile: RenderProfile,
                            out: Path) -> dict[str, Any]:
        """Join the segments with the transition, normalise to the profile's loudness
        target, write a WAV master to ``out``; return the normaliser's measurements."""
        ...

    def render_video_master(self, audio_master: Path, visual: VisualizerSpec,
                            profile: RenderProfile, out: Path) -> None:
        """Encode the visualiser over the background with the audio into ``out``."""
        ...

    def probe(self, path: Path) -> ProbeReport:
        """Container and stream facts of a produced file."""
        ...

    def measure_loudness(self, path: Path) -> LoudnessMeasurement:
        """Integrated loudness and true peak of the file's audio, measured independently
        of the normaliser."""
        ...

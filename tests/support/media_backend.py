"""Test backends for P6: an in-memory artifact store and a stand-in media toolchain.

* :class:`InMemoryArtifactStore` keeps the content-addressing contract of the MinIO
  store (the key ends in the sha256 of the bytes; reads re-verify). Its ``memory://``
  URIs are refused by every real consumer. :meth:`InMemoryArtifactStore.tamper`
  replaces stored bytes without changing the key, as corruption or tampering would.
* :class:`StandInToolchain` does **not** render. It writes placeholder bytes and reports
  a probe and a loudness that conform to the profile (or the overrides a test sets).
  A test using it proves P6's wiring, records and refusals; only the ``media`` tests
  with the FFmpeg toolchain prove rendering.
* :func:`stored_manifest` stores synthetic track audio and returns a C5 manifest whose
  ``artifact_uri`` and ``artifact_sha256`` point at it. ``measured_duration_seconds``
  is the timeline duration the test declares; with the stand-in it need not match the
  audio's length.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_factory.artifacts import (
    SOURCE_ARTIFACT_MISSING,
    SOURCE_CHECKSUM_MISMATCH,
    SOURCE_URI_INVALID,
    StoredArtifact,
)
from media_factory.backend import MediaBackend
from media_factory.brands.registry import BrandRegistry
from media_factory.domain.enums import TransitionType
from media_factory.errors import MediaAssemblyError
from media_factory.rendering.profiles import RenderProfile
from media_factory.rendering.toolchain import (
    AudioMasterSpec,
    LoudnessMeasurement,
    ProbeReport,
    StreamInfo,
    VisualizerSpec,
)
from media_factory.visuals.planner import BrandProfile
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clearance import ClearanceUnavailableError

from tests.support.media_audio import synth_track
from tests.support.seams import release_manifest

__all__ = [
    "FakeHolds",
    "FakeP5",
    "InMemoryArtifactStore",
    "SourceReadingStore",
    "StandInToolchain",
    "backend_for",
    "stored_manifest",
    "synth_specs",
]

_SCHEME = "memory://"


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def _put(self, prefix: str, resource_path: str, data: bytes) -> StoredArtifact:
        digest = hashlib.sha256(data).hexdigest()
        key = f"{prefix}{resource_path}/{digest}"
        self.objects.setdefault(key, data)
        return StoredArtifact(uri=f"{_SCHEME}{key}", sha256=digest, size=len(data))

    def put(self, resource_path: str, data: bytes, *, content_type: str) -> StoredArtifact:
        return self._put("media/", resource_path, data)

    def put_source(self, resource_path: str, data: bytes) -> StoredArtifact:
        """Store a track source as P4 would (under ``music/``)."""
        return self._put("music/", resource_path, data)

    def fetch_verified(self, uri: str, sha256: str) -> bytes:
        if not uri.startswith(_SCHEME):
            raise MediaAssemblyError(SOURCE_URI_INVALID, f"not an in-memory URI: {uri!r}")
        key = uri[len(_SCHEME):]
        if key.rsplit("/", 1)[-1] != sha256:
            raise MediaAssemblyError(SOURCE_CHECKSUM_MISMATCH, f"{uri} is not {sha256}")
        data = self.objects.get(key)
        if data is None:
            raise MediaAssemblyError(SOURCE_ARTIFACT_MISSING, f"no object at {key}")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise MediaAssemblyError(SOURCE_CHECKSUM_MISMATCH, f"{key} does not hash to {sha256}")
        return data

    def get(self, uri: str) -> bytes:
        return self.objects[uri[len(_SCHEME):]]

    def tamper(self, uri: str, data: bytes) -> None:
        self.objects[uri[len(_SCHEME):]] = data


@dataclass
class StandInToolchain:
    """Placeholder rendering with a conforming (or overridden) probe and loudness."""

    probe_overrides: dict[str, Any] = field(default_factory=dict)
    loudness_lufs: float | None = None
    calls: list[str] = field(default_factory=list)
    _durations: dict[str, float] = field(default_factory=dict)
    _profile: RenderProfile | None = None

    @property
    def version(self) -> str:
        return "stand-in (no rendering)"

    def render_audio_master(self, spec: AudioMasterSpec, profile: RenderProfile,
                            out: Path) -> dict[str, Any]:
        self.calls.append("audio")
        offset = {TransitionType.HARD_CUT: 0.0,
                  TransitionType.SILENCE_GAP: spec.transition_seconds}.get(
            spec.transition, -spec.transition_seconds)
        duration = sum(s.duration_seconds for s in spec.segments) + offset * (
            len(spec.segments) - 1)
        digest = hashlib.sha256(b"".join(s.path.read_bytes() for s in spec.segments))
        out.write_bytes(b"STAND-IN AUDIO MASTER " + digest.hexdigest().encode())
        self._durations["audio"] = duration
        self._profile = profile
        return {"filter": "stand-in"}

    def render_video_master(self, audio_master: Path, visual: VisualizerSpec,
                            profile: RenderProfile, out: Path) -> None:
        self.calls.append("video")
        out.write_bytes(b"STAND-IN VIDEO MASTER " + hashlib.sha256(
            audio_master.read_bytes() + visual.background_png.read_bytes()).hexdigest().encode())

    def probe(self, path: Path) -> ProbeReport:
        self.calls.append("probe")
        profile = self._profile
        assert profile is not None
        fields: dict[str, Any] = {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration_seconds": self._durations["audio"],
            "video_stream_duration_seconds": self._durations["audio"],
            "audio_stream_duration_seconds": self._durations["audio"],
            "width": profile.width, "height": profile.height, "fps": float(profile.fps),
            "video_codec": profile.video_codec, "audio_codec": profile.audio_codec,
            "sample_rate": profile.sample_rate, "channels": profile.channels,
            "pixel_format": profile.pixel_format,
        }
        fields.update(self.probe_overrides)
        return ProbeReport(
            format_name=fields["format_name"],
            duration_seconds=fields["duration_seconds"],
            size_bytes=path.stat().st_size,
            streams=(
                StreamInfo(codec_type="video", codec_name=fields["video_codec"],
                           width=fields["width"], height=fields["height"], fps=fields["fps"],
                           pixel_format=fields["pixel_format"],
                           duration_seconds=fields["video_stream_duration_seconds"]),
                StreamInfo(codec_type="audio", codec_name=fields["audio_codec"],
                           sample_rate=fields["sample_rate"], channels=fields["channels"],
                           duration_seconds=fields["audio_stream_duration_seconds"]),
            ),
        )

    def measure_loudness(self, path: Path) -> LoudnessMeasurement:
        self.calls.append("loudness")
        profile = self._profile
        assert profile is not None
        lufs = profile.loudness_target_lufs if self.loudness_lufs is None else self.loudness_lufs
        return LoudnessMeasurement(integrated_lufs=lufs, true_peak_dbtp=profile.true_peak_dbtp,
                                   meter="stand-in")


def synth_specs() -> list[dict[str, Any]]:
    """Three compatible track sources (keys 8B/9B/8A, tempi near 120)."""
    return [
        {"bpm": 120.0, "tonic": 0, "minor": False},
        {"bpm": 122.0, "tonic": 7, "minor": False},
        {"bpm": 118.0, "tonic": 9, "minor": True},
    ]


def stored_manifest(store: InMemoryArtifactStore, candidate_id: str, *,
                    measured_duration_seconds: float, audio_seconds: float = 4.0,
                    synth: dict[str, Any] | None = None,
                    **overrides: Any) -> ReleaseManifestPayload:
    """A cleared C5 manifest whose artifact is real synthetic audio in ``store``."""
    audio = synth_track(seconds=audio_seconds, **(synth or {}))
    stored = store.put_source(f"{candidate_id}/full_mix.wav", audio)
    fields: dict[str, Any] = {
        "candidate_id": candidate_id,
        "artifact_uri": stored.uri,
        "artifact_sha256": stored.sha256,
        "measured_duration_seconds": measured_duration_seconds,
        "track_spec_id": f"trk_{candidate_id}",
    }
    fields.update(overrides)
    return release_manifest(**fields)


class FakeP5:
    """P5's manifest endpoint over a fixed set; ``unavailable`` simulates P5 down."""

    def __init__(self, manifests: list[ReleaseManifestPayload], *,
                 unavailable: bool = False) -> None:
        self.by_id = {m.candidate_id: m for m in manifests}
        self.unavailable = unavailable

    def release_manifest(self, candidate_id: str) -> ReleaseManifestPayload | None:
        if self.unavailable:
            raise ClearanceUnavailableError("P5 unreachable: test")
        return self.by_id.get(candidate_id)


class FakeHolds:
    def __init__(self, codes: dict[str, tuple[str, ...]] | None = None) -> None:
        self.codes = codes or {}

    def open_hold_codes(self, subject_ref: str) -> tuple[str, ...]:
        return self.codes.get(subject_ref, ())


def backend_for(store: InMemoryArtifactStore, manifests: list[ReleaseManifestPayload], *,
                 toolchain: StandInToolchain | None = None, holds: FakeHolds | None = None,
                 brands: BrandRegistry | None = None) -> MediaBackend:
    return MediaBackend(
        manifests=FakeP5(manifests), holds=holds or FakeHolds(), store=store,
        toolchain=toolchain or StandInToolchain(),
        brands=brands or BrandRegistry([BrandProfile(brand_id="brand_neon",
                                                     brand_name="Neon Records")]),
    )


class SourceReadingStore(InMemoryArtifactStore):
    """P6's store over another phase's in-memory storage: track sources are read from
    ``read(uri)`` (for example P4's ``InMemoryArtifactStorage.get_artifact``) and verified
    against the manifest checksum; P6's own outputs go to this in-memory store."""

    def __init__(self, read: Callable[[str], bytes | None]) -> None:
        super().__init__()
        self._read = read

    def fetch_verified(self, uri: str, sha256: str) -> bytes:
        if uri.startswith(_SCHEME + "media/"):
            return super().fetch_verified(uri, sha256)
        data = self._read(uri)
        if data is None:
            raise MediaAssemblyError(SOURCE_ARTIFACT_MISSING, f"nothing stored at {uri}")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise MediaAssemblyError(SOURCE_CHECKSUM_MISMATCH, f"{uri} does not hash to {sha256}")
        return data

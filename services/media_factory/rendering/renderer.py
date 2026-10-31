"""Render orchestration and post-render QA (P6 §8.7, §28, §30, R4).

:class:`MediaRenderer` turns a sequenced playlist and its verified source audio into
stored masters:

1. **Preflight.** Every track has its source bytes and they hash to the track's
   checksum; the visual plan names a background and an accent colour. A failure is a
   refusal (``render_preflight_failed``), nothing is rendered.
2. **Background.** The brand gradient at the profile's size (Pillow), stored and
   recorded as the session's background :class:`~..domain.models.VisualAsset`.
3. **Audio master.** The toolchain joins the tracks with the declared transition and
   normalises loudness to the profile's target (two-pass ``loudnorm``); the WAV is
   stored.
4. **Video master.** The procedural visualiser over the background with the audio
   master, encoded to the profile; the MP4 is stored.
5. **Post-render QA** (:mod:`.qa`) on the produced MP4 via ffprobe and an independent
   loudness meter. A failed check marks the job ``failed`` with the reason codes, and
   the package built from it is blocked.

Every URI and checksum in the result is the URI and sha256 the artifact store returned
for the bytes it stored. A toolchain failure is a refusal with the tool's reason code,
except a timeout, which may clear on retry (:class:`~..errors.MediaInfrastructureError`).
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plm_platform.clock import Clock

from ..artifacts import ArtifactStore, StoredArtifact, scoped_artifact_root
from ..artwork.generator import render_background
from ..domain.enums import RenderJobStatus, VisualAssetType
from ..domain.ids import render_job_id
from ..domain.models import PlaylistSession, RenderJob, VisualAsset, VisualSession
from ..errors import MediaAssemblyError, MediaInfrastructureError
from ..visuals.planner import BrandProfile
from .profiles import RenderProfile
from .qa import PostRenderCheckResult, post_render_qa
from .toolchain import (
    AudioMasterSpec,
    AudioSegment,
    LoudnessMeasurement,
    MediaToolchain,
    MediaToolError,
    ProbeReport,
    VisualizerSpec,
)

__all__ = [
    "RENDER_VERSION",
    "MediaRenderer",
    "PostRenderCheckResult",
    "PreflightCheckResult",
    "RenderExecutionResult",
]

RENDER_VERSION = "p6-render-v1"


@dataclass(frozen=True)
class PreflightCheckResult:
    """Outcome of preflight validation before rendering."""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    input_manifest_hash: str = ""


@dataclass(frozen=True)
class RenderExecutionResult:
    """What a render produced, stored and measured."""

    render_job: RenderJob
    preflight: PreflightCheckResult
    post_render: PostRenderCheckResult
    video_master_uri: str
    video_checksum: str
    audio_master_uri: str
    audio_checksum: str
    duration_seconds: float
    render_report: dict[str, Any]
    visual_assets: list[VisualAsset]

    @property
    def passed(self) -> bool:
        return self.render_job.job_status is RenderJobStatus.COMPLETED


class MediaRenderer:
    """Renders through a :class:`~.toolchain.MediaToolchain` into an artifact store."""

    def __init__(self, *, toolchain: MediaToolchain, store: ArtifactStore,
                 profile: RenderProfile) -> None:
        self.toolchain = toolchain
        self.store = store
        self.profile = profile

    def run_preflight(self, playlist: PlaylistSession, visual_session: VisualSession,
                      sources: Mapping[str, bytes], *,
                      playlist_concept: str = "") -> PreflightCheckResult:
        """Validate inputs before rendering (P6 §28, §29)."""
        reasons: list[str] = []
        if not playlist.tracks:
            reasons.append("the playlist has no tracks")
        for track in playlist.tracks:
            data = sources.get(track.song_id)
            if data is None:
                reasons.append(f"no verified source audio for {track.song_id}")
            elif hashlib.sha256(data).hexdigest() != track.audio_checksum:
                reasons.append(f"source audio of {track.song_id} does not hash to its checksum")
        plan = visual_session.visual_plan_json
        if not (plan.get("visualizer") or {}).get("color"):
            reasons.append("the visual plan names no visualiser colour")
        if len((plan.get("background") or {}).get("colors") or []) < 2:
            reasons.append("the visual plan names no background colours")
        return PreflightCheckResult(
            passed=not reasons, reasons=reasons,
            input_manifest_hash=self.input_manifest_hash(
                playlist, visual_session, playlist_concept=playlist_concept,
            ),
        )

    def input_manifest_hash(
        self, playlist: PlaylistSession, visual_session: VisualSession, *,
        playlist_concept: str = "",
    ) -> str:
        """Stable pre-render identity using the verified source checksums.

        This metadata-only calculation does not fetch every recording again.
        The normal render preflight still checks the source bytes before
        invoking FFmpeg; the identity and check share this implementation.
        """
        manifest = {
            "playlist_id": playlist.id,
            "sequence_checksum": playlist.sequence_checksum,
            "tracks": [[t.id, t.audio_checksum] for t in playlist.tracks],
            "visual_session_id": visual_session.id,
            "visual_plan": visual_session.visual_plan_json,
            "render_profile": self.profile.to_dict(),
            "render_version": RENDER_VERSION,
        }
        if playlist_concept:
            # Thumbnail artwork and metadata depend on this concept even when
            # the audio timeline/visual profile are identical. A staged retry
            # must never accept a changed package presentation as the same input.
            manifest["playlist_concept"] = playlist_concept
        return hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
        ).hexdigest()

    def _store(self, path: str, data: bytes, content_type: str) -> StoredArtifact:
        return self.store.put(path, data, content_type=content_type)

    def render(
        self,
        *,
        media_package_id_str: str,
        playlist: PlaylistSession,
        visual_session: VisualSession,
        brand: BrandProfile,
        sources: Mapping[str, bytes],
        required_window_seconds: tuple[float, float],
        attempt: int = 1,
        claim_namespace: str | None = None,
        playlist_concept: str = "",
        clock: Clock,
    ) -> RenderExecutionResult:
        """Render, store and QA the masters; ``clock`` stamps the job."""
        root = scoped_artifact_root(
            f"renders/{playlist.brand_id}/{media_package_id_str}", claim_namespace,
        )
        started_at = clock.now()
        preflight = self.run_preflight(
            playlist, visual_session, sources, playlist_concept=playlist_concept,
        )
        if not preflight.passed:
            raise MediaAssemblyError("render_preflight_failed", "; ".join(preflight.reasons))
        profile = self.profile
        transition = playlist.tracks[0].transition_type
        transition_seconds = playlist.tracks[0].transition_duration_seconds

        with tempfile.TemporaryDirectory(prefix="plm-render-") as workdir:
            work = Path(workdir)
            segments = []
            for track in playlist.tracks:
                path = work / f"source_{track.position:03d}"
                path.write_bytes(sources[track.song_id])
                segments.append(AudioSegment(
                    path=path,
                    duration_seconds=track.end_offset_seconds - track.start_offset_seconds))

            background_png = render_background(
                brand, profile.width, profile.height,
                colors=tuple(visual_session.visual_plan_json["background"]["colors"][:2]))
            (work / "background.png").write_bytes(background_png)
            background = self._store(f"{root}/background.png", background_png, "image/png")

            try:
                normaliser = self.toolchain.render_audio_master(
                    AudioMasterSpec(segments=tuple(segments), transition=transition,
                                    transition_seconds=transition_seconds),
                    profile, work / "audio_master.wav")
                # Not kept past the upload: an hour of 24-bit stereo is over a gigabyte.
                audio = self._store(f"{root}/audio_master.wav",
                                    (work / "audio_master.wav").read_bytes(), "audio/wav")
                self.toolchain.render_video_master(
                    work / "audio_master.wav",
                    VisualizerSpec(background_png=work / "background.png",
                                   wave_color=str(visual_session.visual_plan_json
                                                  ["visualizer"]["color"])),
                    profile, work / "video_master.mp4")
            except MediaToolError as exc:
                if exc.code == "render_timeout":
                    raise MediaInfrastructureError(exc.code, str(exc)) from exc
                raise MediaAssemblyError(exc.code, str(exc)) from exc
            video_bytes = (work / "video_master.mp4").read_bytes()
            video = self._store(f"{root}/video_master.{profile.container}", video_bytes,
                                "video/mp4")

            probe: ProbeReport | None
            try:
                probe = self.toolchain.probe(work / "video_master.mp4")
            except MediaToolError:
                probe = None
            loudness: LoudnessMeasurement | None
            try:
                loudness = self.toolchain.measure_loudness(work / "video_master.mp4")
            except MediaToolError:
                loudness = None

        post = post_render_qa(
            probe=probe, loudness=loudness, profile=profile,
            expected_duration_seconds=playlist.actual_duration_seconds,
            required_window_seconds=required_window_seconds, size_bytes=video.size,
        )
        finished_at = clock.now()
        # A corrupt FFprobe number is already a QA failure. Never persist
        # NaN/Infinity as the package's structural duration or JSON metadata.
        measured_duration = probe.duration_seconds if probe is not None else None
        duration = (measured_duration if measured_duration is not None
                    and math.isfinite(measured_duration) and measured_duration > 0
                    else playlist.actual_duration_seconds)
        job = RenderJob(
            id=render_job_id(media_package_id_str, profile.name, attempt),
            media_package_id=media_package_id_str,
            render_profile=profile.name,
            render_version=RENDER_VERSION,
            job_status=RenderJobStatus.COMPLETED if post.passed else RenderJobStatus.FAILED,
            priority="NORMAL",
            attempt_count=attempt,
            worker_id=None,
            started_at=started_at,
            finished_at=finished_at,
            input_manifest_hash=preflight.input_manifest_hash,
            output_checksum=video.sha256,
            error_code=None if post.passed else "post_render_qa_failed",
            error_detail=None if post.passed else "; ".join(post.reasons),
            created_at=started_at,
        )
        background_asset = VisualAsset.create(
            session_id=visual_session.id,
            asset_type=VisualAssetType.BACKGROUND,
            uri=background.uri,
            checksum=background.sha256,
            width=profile.width,
            height=profile.height,
            duration_seconds=duration,
            fps=float(profile.fps),
            source_type="procedural_gradient",
            provider="media_factory",
            provider_asset_id=f"background:{brand.brand_id}:{brand.version}",
            license_status="internal",
            provenance_id=f"render:{job.id}",
            now=started_at,
        )
        report = {
            "render_job_id": job.id,
            "artifact_claim_namespace": claim_namespace,
            "render_version": RENDER_VERSION,
            "render_profile": profile.to_dict(),
            "toolchain": self.toolchain.version,
            "audio_pipeline": {
                "transition": transition.value,
                "transition_seconds": transition_seconds,
                "normaliser": normaliser,
            },
            "video_pipeline": {
                "visualizer": visual_session.visual_plan_json.get("visualizer"),
                "background": {"uri": background.uri, "checksum": background.sha256},
            },
            "audio_master": {"uri": audio.uri, "checksum": audio.sha256, "bytes": audio.size},
            "video_master": {"uri": video.uri, "checksum": video.sha256, "bytes": video.size},
            "duration_seconds": round(duration, 3),
            "timeline_seconds": round(playlist.actual_duration_seconds, 3),
            "qa": {
                "passed": post.passed,
                "reason_codes": post.reason_codes,
                "reasons": post.reasons,
                "metrics": post.metrics,
            },
            "status": "passed" if post.passed else "failed",
        }
        return RenderExecutionResult(
            render_job=job,
            preflight=preflight,
            post_render=post,
            video_master_uri=video.uri,
            video_checksum=video.sha256,
            audio_master_uri=audio.uri,
            audio_checksum=audio.sha256,
            duration_seconds=duration,
            render_report=report,
            visual_assets=[background_asset],
        )

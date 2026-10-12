"""Media Package Assembler and Orchestrator (P6 §5, §8, §15, §28, Ruling R9).

Coordinates playlist assembly, visual planning, artwork/thumbnail generation,
metadata compilation, deterministic rendering, and packaging into the frozen Seam C6 contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..artwork.generator import generate_artwork_and_thumbnails
from ..domain.enums import PackageStatus, TransitionType
from ..domain.models import (
    MediaPackage,
    PlaylistSession,
    PlaylistTrack,
    RenderJob,
    ThumbnailVariant,
    VisualAsset,
    VisualSession,
)
from ..metadata.compiler import compile_metadata_and_chapters
from ..playlist.assembler import assemble_playlist
from ..visuals.planner import BrandProfile, plan_visual_session
from .renderer import DeterministicRenderer, RenderExecutionResult

__all__ = ["AssemblyPipelineResult", "assemble_media_package"]


@dataclass(frozen=True)
class AssemblyPipelineResult:
    """Complete product of the media factory assembly pipeline."""

    media_package: MediaPackage
    playlist_session: PlaylistSession
    playlist_tracks: list[PlaylistTrack]
    visual_session: VisualSession
    visual_assets: list[VisualAsset]
    thumbnail_variants: list[ThumbnailVariant]
    render_job: RenderJob
    render_result: RenderExecutionResult


def assemble_media_package(
    *,
    release_manifest: dict[str, Any],
    brand_profile: BrandProfile,
    render_profile: str = "yt_longform_1080p_v1",
    required_duration_seconds: float = 3600.0,
    sequence_version: str = "v1",
    transition_type: TransitionType = TransitionType.SHORT_CROSSFADE,
    transition_duration_seconds: float = 2.0,
    renderer: DeterministicRenderer | None = None,
) -> AssemblyPipelineResult:
    """Orchestrate end-to-end media packaging conforming to Seam C6 Hop 1 (Ruling R9)."""
    # 1. Extract core lineage & manifest attributes (Ruling R9)
    rel_id = release_manifest.get("release_manifest_id") or release_manifest.get(
        "p5_manifest_id", ""
    )
    exp_id = release_manifest.get("experiment_id", "")
    arm_id = release_manifest.get("content_arm_id", "")
    brand_id = release_manifest.get("brand_id", brand_profile.brand_id)

    if not rel_id or not exp_id or not arm_id:
        raise ValueError(
            f"Release manifest missing required lineage identifiers: "
            f"release_manifest_id={rel_id}, experiment_id={exp_id}, content_arm_id={arm_id} — Ruling R9."
        )

    # 2. Extract candidate manifests and track details
    # P5 manifests can supply tracks either via 'tracks' or 'candidate_manifests'/'candidates'
    raw_tracks = (
        release_manifest.get("tracks")
        or release_manifest.get("candidates")
        or release_manifest.get("candidate_manifests")
        or []
    )

    candidate_manifests: list[dict[str, Any]] = []
    track_details: list[dict[str, Any]] = []

    for t in raw_tracks:
        cand_id = t.get("candidate_id") or t.get("song_id", "")
        cand_manifest = {
            "candidate_id": cand_id,
            "disposition": t.get("disposition", t.get("provenance_status", "PASSED")),
            "canonical_artifact_id": t.get(
                "canonical_artifact_id", t.get("artifact_id", f"art_{cand_id}")
            ),
        }
        candidate_manifests.append(cand_manifest)

        track_detail = {
            "candidate_id": cand_id,
            "title": t.get("title", f"Track {cand_id}"),
            "duration_seconds": float(t.get("duration_seconds", 180.0)),
            "artifact_id": cand_manifest["canonical_artifact_id"],
            "audio_uri": t.get("audio_uri", f"media/audio/{cand_id}.wav"),
            "audio_checksum": t.get("audio_checksum", f"chk_{cand_id}"),
        }
        track_details.append(track_detail)

    # 3. Assemble Playlist Session (P6 §4, §9, §10, Rulings R7, R8)
    playlist_res = assemble_playlist(
        release_manifest_id=rel_id,
        experiment_id=exp_id,
        content_arm_id=arm_id,
        brand_id=brand_id,
        candidate_manifests=candidate_manifests,
        track_details=track_details,
        required_duration_seconds=required_duration_seconds,
        transition_type=transition_type,
        transition_duration_seconds=transition_duration_seconds,
        sequence_version=sequence_version,
    )
    playlist_session = playlist_res.session
    tracks = playlist_res.tracks

    # 4. Plan Visual Session (P6 §12-§15, §25)
    visual_profile = release_manifest.get("visual_profile", f"{brand_id}_rain_v1")
    visual_res = plan_visual_session(
        playlist_session_id_str=playlist_session.id,
        brand_profile=brand_profile,
        visual_profile=visual_profile,
        total_duration_seconds=playlist_res.total_duration_seconds,
    )
    visual_session = visual_res.visual_session
    visual_assets = visual_res.assets

    # 5. Deterministic Rendering (P6 §28-§31)
    active_renderer = renderer or DeterministicRenderer(render_profile=render_profile)
    # Temporary mock media_package_id for rendering seed
    pkg_id_seed = f"mp_{exp_id}_{brand_id}_{sequence_version}"
    render_res = active_renderer.render(
        media_package_id_str=pkg_id_seed,
        playlist=playlist_session,
        visual_session=visual_session,
    )

    # 6. Artwork & Thumbnail Generation (P6 §8.5, §17, §18)
    playlist_concept = release_manifest.get(
        "playlist_intent", f"{brand_profile.brand_name} Session"
    )
    artwork_res = generate_artwork_and_thumbnails(
        media_package_id_str=pkg_id_seed,
        brand_profile=brand_profile,
        playlist_title=playlist_concept,
    )
    thumbnail_variants = artwork_res.thumbnails

    # 7. Metadata, Chapter & Tracklist Compilation (P6 §13, §45, §178)
    ai_disclosure = bool(
        release_manifest.get("policy_flags", {}).get("ai_disclosure_required", True)
    )
    meta_res = compile_metadata_and_chapters(
        tracks=tracks,
        brand_name=brand_profile.brand_name,
        playlist_concept=playlist_concept,
        ai_disclosure_required=ai_disclosure,
    )

    # 8. Short-Form Source Assets (P6 §15)
    short_source_assets = [
        {
            "clip_id": f"short_{t.song_id}_01",
            "source_track_id": t.song_id,
            "start_seconds": t.start_offset_seconds,
            "duration_seconds": min(30.0, t.end_offset_seconds - t.start_offset_seconds),
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "recommended_hook": f"Best moment of {t.title}",
        }
        for t in tracks[:3]
    ]

    # Convert thumbnail variants to Seam C6 Hop 1 producer schema [{id, uri}, ...]
    thumbnails_c6 = [
        {
            "id": tv.variant_key,
            "uri": tv.uri,
            "checksum": tv.checksum,
            "width": tv.width,
            "height": tv.height,
        }
        for tv in thumbnail_variants
    ]

    # 9. Final MediaPackage instantiation (Seam C6 Hop 1, Ruling R9)
    package_status = (
        PackageStatus.READY_FOR_PUBLISH
        if render_res.render_job.job_status.value == "completed"
        else PackageStatus.FAILED
    )

    media_package = MediaPackage.create(
        release_manifest_id=rel_id,
        experiment_id=exp_id,
        content_arm_id=arm_id,
        brand_id=brand_id,
        playlist_session_id=playlist_session.id,
        visual_session_id=visual_session.id,
        video_master_uri=render_res.video_master_uri,
        video_checksum=render_res.video_checksum,
        video_duration_seconds=render_res.duration_seconds,
        render_profile=render_profile,
        audio_master_uri=render_res.audio_master_uri,
        audio_checksum=render_res.audio_checksum,
        thumbnails=thumbnails_c6,
        tracklist=meta_res.tracklist,
        chapters=meta_res.chapters,
        metadata_candidates=meta_res.metadata_candidates,
        short_source_assets=short_source_assets,
        render_report=render_res.render_report,
        package_version=sequence_version,
        status=package_status,
    )

    return AssemblyPipelineResult(
        media_package=media_package,
        playlist_session=playlist_session,
        playlist_tracks=tracks,
        visual_session=visual_session,
        visual_assets=visual_assets,
        thumbnail_variants=thumbnail_variants,
        render_job=render_res.render_job,
        render_result=render_res,
    )

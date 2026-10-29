"""Media package assembly (P6 §5, §8, §28, Rulings R7, R9, ADR-0028, R4).

From P5's per-track C5 manifests to a C6 media package, in this order, each step
refusing with a reason code rather than guessing:

1. clearance and lineage (R7, R8): every track cleared, one lineage;
2. the source audio of every track, fetched by ``artifact_uri`` and verified against
   ``artifact_sha256`` (a mismatch fails closed);
3. sequencing features measured from those bytes (:mod:`..audio.features`);
4. selection and order by beam search, landing inside the declared playlist range
   (:mod:`..playlist.sequencer`);
5. chapters compiled from the final timeline and validated against YouTube's rules;
6. the render: audio master, procedural video master, both stored, then post-render
   QA via ffprobe and a loudness meter (:mod:`.renderer`);
7. thumbnails A/B/C drawn and stored (:mod:`..artwork.generator`);
8. the package, ``ready_for_publish`` only when the render passed QA, else ``failed``
   with the QA reason codes in its render report.

Every URI and checksum in the package is what the artifact store returned for bytes P6
produced and stored.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clock import Clock

from ..artifacts import ArtifactStore
from ..artwork.generator import generate_thumbnails
from ..audio.features import (
    DEFAULT_FEATURE_POLICY,
    FeatureExtractionPolicy,
    TrackFeatures,
    measure_features,
)
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
from ..metadata.chapters import require_valid_chapters
from ..metadata.compiler import compile_metadata_and_chapters
from ..playlist.assembler import assemble_playlist, check_clearance
from ..playlist.catalog import CatalogAlbum, check_catalog_texture
from ..playlist.diversity import check_album_texture
from ..playlist.sequencer import DEFAULT_SEQUENCING_POLICY, SequencingPolicy
from ..visuals.planner import BrandProfile, plan_visual_session
from .profiles import RenderProfile, render_profile
from .renderer import MediaRenderer, RenderExecutionResult
from .toolchain import MediaToolchain

__all__ = ["AssemblyPipelineResult", "PackageAlreadyAssembled", "assemble_media_package"]


class PackageAlreadyAssembled(Exception):
    """The package this request resolves to exists already; nothing was rendered."""

    def __init__(self, package: MediaPackage) -> None:
        self.package = package
        super().__init__(package.media_package_id)


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


class _FetchOnRead(Mapping[str, bytes]):
    """Recording bytes by candidate id, fetched and verified on every read, never kept."""

    def __init__(self, store: ArtifactStore,
                 manifests: Mapping[str, ReleaseManifestPayload]) -> None:
        self._store = store
        self._manifests = dict(manifests)

    def __getitem__(self, candidate_id: str) -> bytes:
        manifest = self._manifests[candidate_id]
        return self._store.fetch_verified(manifest.artifact_uri, manifest.artifact_sha256)

    def __iter__(self) -> Iterator[str]:
        return iter(self._manifests)

    def __len__(self) -> int:
        return len(self._manifests)


def assemble_media_package(
    *,
    track_manifests: Sequence[ReleaseManifestPayload],
    brand_profile: BrandProfile,
    duration_range: DurationRangeSeconds,
    visual_profile: str,
    playlist_concept: str,
    store: ArtifactStore,
    toolchain: MediaToolchain,
    render_profile_name: str | RenderProfile = "yt_longform_1080p_v1",
    sequencing_policy: SequencingPolicy = DEFAULT_SEQUENCING_POLICY,
    feature_policy: FeatureExtractionPolicy = DEFAULT_FEATURE_POLICY,
    transition_type: TransitionType = TransitionType.SHORT_CROSSFADE,
    transition_duration_seconds: float = 2.0,
    existing: Callable[[str], MediaPackage | None] | None = None,
    catalog_albums: Sequence[CatalogAlbum] | None = None,
    catalog_loader: Callable[[], Sequence[CatalogAlbum]] | None = None,
    before_render: Callable[[str, str, str], str] | None = None,
    render_attempt: int = 1,
    clock: Clock,
) -> AssemblyPipelineResult:
    """Assemble, render and package P5's cleared tracks (R7, R9, ADR-0028, R4).

    ``duration_range`` (P3's declared playlist range), ``visual_profile`` and
    ``playlist_concept`` come from P3's playlist, visual and title specs; they have no
    defaults here. ``existing`` looks
    a package id up: when the request resolves to a package already assembled,
    :class:`PackageAlreadyAssembled` is raised before anything is rendered.
    """
    profile = (render_profile_name if isinstance(render_profile_name, RenderProfile)
               else render_profile(render_profile_name))
    brand_id = brand_profile.brand_id
    planned_at = clock.now()

    # 1-2. Clearance first, so nothing uncleared is even fetched; then verified bytes.
    check_clearance(track_manifests)
    # 3-4. Measured features (one recording in memory at a time), then selection and
    # order. Holding every offered recording at once made memory grow with the supply
    # (R5: about 60 cleared tracks for one long playlist, and the render OOM-killed).
    features: dict[str, TrackFeatures] = {}
    for manifest in track_manifests:
        features[manifest.candidate_id] = measure_features(
            manifest.candidate_id,
            store.fetch_verified(manifest.artifact_uri, manifest.artifact_sha256),
            feature_policy)
    playlist_res = assemble_playlist(
        manifests=track_manifests,
        features=features,
        brand_id=brand_id,
        duration_range=duration_range,
        transition_type=transition_type,
        transition_duration_seconds=transition_duration_seconds,
        policy=sequencing_policy,
        now=planned_at,
    )
    playlist_session = playlist_res.session
    tracks = playlist_res.tracks
    lineage = playlist_res.lineage
    rel_id = playlist_session.release_manifest_id
    package_version = sequencing_policy.version
    package_id = MediaPackage.derive_id(rel_id, brand_id, package_version, profile.name)
    if existing is not None:
        found = existing(package_id)
        if found is not None:
            raise PackageAlreadyAssembled(found)

    # 5. Chapters from the final timeline, validated before any render cost is spent.
    ai_disclosure = any(m.ai_disclosure_required for m in playlist_res.manifests)
    meta_res = compile_metadata_and_chapters(
        tracks=tracks,
        brand_name=brand_profile.brand_name,
        playlist_concept=playlist_concept,
        ai_disclosure_required=ai_disclosure,
    )
    require_valid_chapters(meta_res.chapter_objects, playlist_res.total_duration_seconds)

    # Every known format/timeline error has precedence. Then refuse a likely
    # near-copy album before spending render resources or writing artifacts.
    selected_ids = [track.song_id for track in tracks]
    texture_report = check_album_texture(selected_ids, features)
    checksums = {item.candidate_id: item.artifact_sha256 for item in track_manifests}
    if catalog_loader is not None and catalog_albums is not None:
        raise ValueError("provide either a catalog loader or catalog albums, not both")
    # Lazy lookup is essential: a replay of an already assembled package must
    # succeed even when unrelated historical albums need remediation.
    recorded = catalog_loader() if catalog_loader is not None else catalog_albums
    catalog_report = check_catalog_texture(
        selected_ids, features, checksums,
        current_release_manifest_id=rel_id,
        history=recorded if recorded is not None else (),
    )
    # The assembly service supplies historical evidence; direct in-process calls
    # without a catalogue are explicitly marked as unverified.
    texture_report["evidence"] = catalog_report.pop("evidence")
    texture_report["catalog"] = {
        **catalog_report,
        "coverage": "complete" if recorded is not None else "not_configured",
    }

    # 6. Visual plan and render.
    visual_res = plan_visual_session(
        playlist_session_id_str=playlist_session.id,
        brand_profile=brand_profile,
        visual_profile=visual_profile,
        render_profile=profile,
        total_duration_seconds=playlist_res.total_duration_seconds,
        now=planned_at,
    )
    visual_session = visual_res.visual_session
    by_candidate = {m.candidate_id: m for m in track_manifests}
    renderer = MediaRenderer(toolchain=toolchain, store=store, profile=profile)
    claim_namespace: str | None = None
    if before_render is not None:
        # Commit the durable job and its single-worker claim before FFmpeg or
        # any output-blob write. This fingerprint is the renderer's exact
        # metadata identity, using hashes of sources verified during feature
        # extraction, without loading all selected audio a second time.
        claim_namespace = before_render(
            package_id, profile.name,
            renderer.input_manifest_hash(
                playlist_session, visual_session, playlist_concept=playlist_concept,
            ),
        )
    render_res = renderer.render(
        media_package_id_str=package_id,
        playlist=playlist_session,
        visual_session=visual_session,
        brand=brand_profile,
        # Only the recordings the sequence selected, fetched (and verified) again on each
        # read, so the render holds one recording at a time.
        sources=_FetchOnRead(store, {t.song_id: by_candidate[t.song_id] for t in tracks}),
        required_window_seconds=playlist_res.sequence.window,
        claim_namespace=claim_namespace,
        attempt=render_attempt,
        playlist_concept=playlist_concept,
        clock=clock,
    )
    visual_session.assets = list(render_res.visual_assets)
    visual_session.status = "rendered" if render_res.passed else "failed"
    packaged_at = clock.now()

    # 7. Thumbnails.
    thumbnail_variants = generate_thumbnails(
        media_package_id_str=package_id,
        brand_profile=brand_profile,
        playlist_concept=playlist_concept,
        store=store,
        claim_namespace=claim_namespace,
        now=packaged_at,
    )

    # 8. Short-form source descriptors (clip extraction itself is not rendered yet).
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
    thumbnails_c6 = [
        {"id": tv.variant_key, "uri": tv.uri, "checksum": tv.checksum,
         "width": tv.width, "height": tv.height}
        for tv in thumbnail_variants
    ]
    render_report = {
        **render_res.render_report,
        "sequencing": playlist_res.sequence.report,
        "album_texture": texture_report,
        "chapters": {"rules": "yt-chapters-v1", "count": len(meta_res.chapters)},
    }
    media_package = MediaPackage.create(
        release_manifest_id=rel_id,
        experiment_id=lineage.experiment_id,
        content_arm_id=lineage.content_arm_id,
        brand_id=brand_id,
        playlist_session_id=playlist_session.id,
        visual_session_id=visual_session.id,
        video_master_uri=render_res.video_master_uri,
        video_checksum=render_res.video_checksum,
        video_duration_seconds=render_res.duration_seconds,
        render_profile=profile.name,
        audio_master_uri=render_res.audio_master_uri,
        audio_checksum=render_res.audio_checksum,
        thumbnails=thumbnails_c6,
        tracklist=meta_res.tracklist,
        chapters=meta_res.chapters,
        metadata_candidates=meta_res.metadata_candidates,
        short_source_assets=short_source_assets,
        render_report=render_report,
        lineage=lineage,
        package_version=package_version,
        status=PackageStatus.READY_FOR_PUBLISH if render_res.passed else PackageStatus.FAILED,
        now=packaged_at,
    )
    if media_package.media_package_id != package_id:
        raise RuntimeError("the package id derivation diverged from the rendered id")

    return AssemblyPipelineResult(
        media_package=media_package,
        playlist_session=playlist_session,
        playlist_tracks=tracks,
        visual_session=visual_session,
        visual_assets=list(render_res.visual_assets),
        thumbnail_variants=thumbnail_variants,
        render_job=render_res.render_job,
        render_result=render_res,
    )

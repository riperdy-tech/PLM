"""Controlled production dry-run smoke test for PHASE-P6 (P6 §45, Ruling R9, R4).

Runs the P6 pipeline on the deployed backends with synthetic sources: three short
generated tones (each a distinct key and tempo) are stored in the artifact store, C5
manifests point at them, and P6 verifies, measures, sequences, renders with the preview
profile, QA-checks, draws thumbnails and persists the package. The manifests are built
in-process (this smoke exercises P6 alone, not P5); the audio and every output are
real stored bytes.

``store`` and ``toolchain`` default to MinIO and FFmpeg; tests inject their own.
"""

from __future__ import annotations

import io
import wave

import numpy as np
from plm_contracts import CommercialUseEligibility, GateDisposition, ProvenanceStatus
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clock import SystemClock
from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..artifacts import ArtifactStore, MinioArtifactStore
from ..domain.enums import PackageStatus
from ..errors import MediaAssemblyError, MediaInfrastructureError
from ..rendering.assembler import assemble_media_package
from ..rendering.ffmpeg import FFmpegToolchain
from ..rendering.toolchain import MediaToolchain
from ..storage.repositories import (
    get_media_package_by_id,
    insert_media_package,
    insert_playlist_session,
    insert_playlist_track,
    insert_render_job,
    insert_thumbnail_variant,
    insert_visual_asset,
    insert_visual_session,
)
from ..visuals.planner import BrandProfile

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending media factory service token (PLM_SERVICE_TOKEN)"
_TRACK_SECONDS = 12.0
_RATE = 44100


def _tone(seconds: float, frequency: float, bpm: float) -> bytes:
    t = np.arange(int(seconds * _RATE)) / _RATE
    signal = 0.3 * np.sin(2 * np.pi * frequency * t) + 0.15 * np.sin(3 * np.pi * frequency * t)
    beat = (t % (60.0 / bpm)) < 0.03
    signal = signal + 0.3 * beat * np.sin(2 * np.pi * 90.0 * t)
    pcm = (np.clip(signal, -1.0, 1.0) * 32767 * 0.5).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(_RATE)
        out.writeframes(pcm.tobytes())
    return buffer.getvalue()


def run_smoke_test(*, store: ArtifactStore | None = None,
                   toolchain: MediaToolchain | None = None) -> str | None:
    """Run the P6 dry-run smoke test. Returns None on success, or blocked reason string."""
    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON

    engine = build_engine(platform)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return f"blocked: database unreachable ({exc})"

    clock = SystemClock()
    ts = int(clock.now().timestamp())
    active_store = store if store is not None else MinioArtifactStore(platform)
    active_toolchain = toolchain if toolchain is not None else FFmpegToolchain()
    lineage = ExperimentLineage(
        experiment_id=f"exp_smoke_{ts}",
        experiment_family_id="fam_smoke_01",
        content_arm_id=f"arm_smoke_{ts}",
        content_genome_id="gen_smoke_01",
        production_directive_id=f"dir_smoke_{ts}",
    )
    try:
        track_manifests = []
        for n, frequency, bpm in (("01", 261.63, 120.0), ("02", 392.0, 122.0),
                                  ("03", 220.0, 118.0)):
            stored = active_store.put(f"smoke/song_smoke_{ts}_{n}/source.wav",
                                      _tone(_TRACK_SECONDS, frequency, bpm),
                                      content_type="audio/wav")
            track_manifests.append(ReleaseManifestPayload.build(
                p5_manifest_id=f"rel_man_smoke_{ts}_{n}",
                release_manifest_version="1.0",
                candidate_id=f"song_smoke_{ts}_{n}",
                canonical_artifact_id=f"art_smoke_{ts}_{n}",
                artifact_uri=stored.uri,
                artifact_sha256=stored.sha256,
                measured_duration_seconds=_TRACK_SECONDS,
                track_spec_id=f"trk_smoke_{n}",
                lineage=lineage,
                disposition=GateDisposition.PASSED,
                override=None,
                technical_status="PASSED",
                commercial_use_status=CommercialUseEligibility.ELIGIBLE,
                provenance_status=ProvenanceStatus.COMPLETE,
                ai_disclosure_required=True,
                rights_risk="LOW",
                policy_status="COMPLIANT",
                evaluation_id=f"eval_smoke_{ts}_{n}",
                evaluation_policy_version="p5-v1",
                warning_codes=(),
                evidence_bundle_id=f"evi_smoke_{ts}_{n}",
            ))

        res = assemble_media_package(
            track_manifests=track_manifests,
            brand_profile=BrandProfile(brand_id="brand_smoke", brand_name="PLM Smoke"),
            required_duration_seconds=3 * _TRACK_SECONDS - 4.0,
            visual_profile="smoke_waveform_v1",
            playlist_concept="Smoke Test Session",
            store=active_store,
            toolchain=active_toolchain,
            render_profile_name="preview_360p_v1",
            clock=clock,
        )
    except (MediaAssemblyError, MediaInfrastructureError) as exc:
        return f"smoke test failed: {exc}"

    if res.media_package.status != PackageStatus.READY_FOR_PUBLISH:
        codes = (res.media_package.render_report.get("qa") or {}).get("reason_codes")
        return f"smoke test failed: package {res.media_package.status.value} ({codes})"

    factory = session_factory(engine)
    try:
        with factory() as session:
            insert_playlist_session(session, res.playlist_session)
            for tr in res.playlist_tracks:
                insert_playlist_track(session, tr)
            insert_visual_session(session, res.visual_session)
            for va in res.visual_assets:
                insert_visual_asset(session, va)
            for tv in res.thumbnail_variants:
                insert_thumbnail_variant(session, tv)
            insert_render_job(session, res.render_job)
            insert_media_package(session, res.media_package)
            session.commit()
            fetched = get_media_package_by_id(session, res.media_package.media_package_id)
            if fetched is None or fetched.video_checksum != res.media_package.video_checksum:
                return "smoke test failed: persisted media package could not be read back"
    except Exception as exc:  # noqa: BLE001
        return f"smoke test raised exception: {exc}"
    return None

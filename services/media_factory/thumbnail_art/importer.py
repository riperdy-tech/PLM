"""Attach the owner's thumbnail art to a waiting package and release it for publishing.

All three images are checked before anything is stored: one missing or refused image
leaves the package waiting, with every problem named. The video is then re-rendered as
art A held still over the package's audio master (the owner's rule, 2026-11-04: the
picture is the thumbnail, with no waveform or motion) and passes the same post-render QA
as the first render, or the package keeps waiting. On success the package offers only
the owner's art (A first, the style P2 chose), carries the still video, and goes
``ready_for_publish``.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..artifacts import ArtifactStore
from ..domain.enums import PackageStatus
from ..domain.models import MediaPackage, ThumbnailVariant
from ..errors import MediaAssemblyError
from ..rendering.profiles import STILL_VIDEO_PROFILES, render_profile
from ..rendering.qa import post_render_qa
from ..rendering.toolchain import MediaToolchain, MediaToolError
from ..storage.repositories import (
    get_media_package_by_id,
    insert_thumbnail_variant,
    list_thumbnail_variants,
    replace_media_package_art,
)
from .inbox import SIZE, check_image, find_art, read_job

__all__ = ["import_owner_art"]

_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg"}


def import_owner_art(session: Session, store: ArtifactStore, toolchain: MediaToolchain,
                     package_id: str, folder: Path, *, now: datetime) -> MediaPackage:
    pkg = get_media_package_by_id(session, package_id)
    if pkg is None:
        raise MediaAssemblyError("media_package_unknown", f"no media package {package_id!r}")
    if pkg.status is not PackageStatus.AWAITING_THUMBNAIL_ART:
        raise MediaAssemblyError(
            "thumbnail_art_not_awaited",
            f"{package_id} is {pkg.status.value}, not {PackageStatus.AWAITING_THUMBNAIL_ART.value}")
    job = read_job(folder)
    if job.get("media_package_id") != package_id:
        raise MediaAssemblyError("thumbnail_art_job_mismatch",
                                 f"{folder} holds the job of {job.get('media_package_id')!r}")

    checked, problems = [], []
    for variant in job["variants"]:
        key = variant["variant_key"]
        path = find_art(folder, key)
        if path is None:
            problems.append(f"{key}: no image saved (expected {key[-1].upper()}.png)")
            continue
        result = check_image(path.read_bytes())
        if result.problem is not None:
            problems.append(f"{key} ({path.name}): {result.problem}")
        else:
            checked.append((variant, result, path))
    if problems:
        raise MediaAssemblyError("thumbnail_art_invalid", "; ".join(problems))
    video = _render_still_video(store, toolchain, pkg, checked[0][0]["variant_key"],
                                checked[0][2])

    existing = {t.variant_key: t for t in list_thumbnail_variants(session, package_id)}
    offered = []
    for variant, result, _ in checked:
        key = variant["variant_key"]
        stored = store.put(
            f"thumbnails/{pkg.brand_id}/{package_id}/owner_art/{key}"
            f"{_EXTENSIONS[result.content_type]}", result.data,
            content_type=result.content_type)
        prior = existing.get(key)
        if prior is not None and prior.checksum != stored.sha256:
            raise MediaAssemblyError(
                "thumbnail_art_conflict", f"{key} was already imported with other bytes")
        if prior is None:
            insert_thumbnail_variant(session, ThumbnailVariant.create(
                media_package_id_str=package_id, variant_key=key,
                source_assets=[f"owner_art:{variant['style_id']}"], uri=stored.uri,
                checksum=stored.sha256, text_payload=variant["text"],
                width=SIZE[0], height=SIZE[1],
                layout_version=f"owner_art+{job['guide_version']}", now=now))
        offered.append({"id": key, "uri": stored.uri, "checksum": stored.sha256,
                        "width": SIZE[0], "height": SIZE[1]})
    assert pkg.lineage is not None  # an assembled package always has lineage
    manifest_hash = MediaPackage.compute_manifest_hash(
        media_package_id=package_id, release_manifest_id=pkg.release_manifest_id,
        experiment_id=pkg.experiment_id, content_arm_id=pkg.content_arm_id,
        lineage=pkg.lineage, brand_id=pkg.brand_id, video_checksum=video["checksum"],
        audio_checksum=pkg.audio_checksum, package_version=pkg.package_version)
    if not replace_media_package_art(
            session, package_id, offered, video_master_uri=video["uri"],
            video_checksum=video["checksum"], video_duration_seconds=video["duration"],
            render_report=video["report"], manifest_hash=manifest_hash,
            expected_status=PackageStatus.AWAITING_THUMBNAIL_ART,
            status=PackageStatus.READY_FOR_PUBLISH, now=now):
        raise MediaAssemblyError("thumbnail_art_not_awaited",
                                 f"{package_id} left {PackageStatus.AWAITING_THUMBNAIL_ART.value}")
    released = get_media_package_by_id(session, package_id)
    assert released is not None
    return released


def _render_still_video(store: ArtifactStore, toolchain: MediaToolchain, pkg: MediaPackage,
                        variant_key: str, art: Path) -> dict[str, Any]:
    """``art`` (the owner's original file, not the 1280 px thumbnail) held still over the
    package's audio master, QA'd, then stored; refused, with nothing stored, unless QA holds."""
    profile = render_profile(STILL_VIDEO_PROFILES.get(pkg.render_profile, pkg.render_profile))
    report = dict(pkg.render_report)
    timeline = float(report["timeline_seconds"])
    low, high = report["sequencing"]["window_seconds"]
    with tempfile.TemporaryDirectory(prefix="plm-still-") as workdir:
        work = Path(workdir)
        audio = work / "audio_master.wav"
        audio.write_bytes(store.fetch_verified(pkg.audio_master_uri, pkg.audio_checksum))
        image = work / f"art{art.suffix.lower()}"
        image.write_bytes(art.read_bytes())
        out = work / f"video_master.{profile.container}"
        try:
            toolchain.render_still_video(audio, image, profile, out)
            probe = toolchain.probe(out)
            loudness = toolchain.measure_loudness(out)
        except MediaToolError as exc:
            raise MediaAssemblyError("thumbnail_art_video_failed", str(exc)) from exc
        qa = post_render_qa(probe=probe, loudness=loudness, profile=profile,
                            expected_duration_seconds=timeline,
                            required_window_seconds=(float(low), float(high)),
                            size_bytes=out.stat().st_size)
        if not qa.passed:
            raise MediaAssemblyError("thumbnail_art_video_failed", "; ".join(qa.reasons))
        stored = store.put(
            f"renders/{pkg.brand_id}/{pkg.media_package_id}/owner_art/"
            f"video_master.{profile.container}", out.read_bytes(), content_type="video/mp4")
    duration = probe.duration_seconds if probe.duration_seconds is not None else timeline
    report["video_pipeline"] = {
        "still_image": {"variant_key": variant_key, "source": art.name,
                        "render_profile": profile.name},
        "replaced": {"video_pipeline": pkg.render_report.get("video_pipeline"),
                     "video_master": pkg.render_report.get("video_master")},
    }
    report["toolchain"] = toolchain.version
    report["video_master"] = {"uri": stored.uri, "checksum": stored.sha256, "bytes": stored.size}
    report["duration_seconds"] = round(duration, 3)
    report["qa"] = {"passed": True, "reason_codes": [], "reasons": [], "metrics": qa.metrics}
    return {"uri": stored.uri, "checksum": stored.sha256, "duration": duration,
            "report": report}

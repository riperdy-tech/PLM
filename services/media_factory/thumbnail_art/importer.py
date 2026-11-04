"""Attach the owner's thumbnail art to a waiting package and release it for publishing.

All three images are checked before anything is stored: one missing or refused image
leaves the package waiting, with every problem named. On success the package offers
only the owner's art (A first, the style P2 chose) and goes ``ready_for_publish``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..artifacts import ArtifactStore
from ..domain.enums import PackageStatus
from ..domain.models import MediaPackage, ThumbnailVariant
from ..errors import MediaAssemblyError
from ..storage.repositories import (
    get_media_package_by_id,
    insert_thumbnail_variant,
    list_thumbnail_variants,
    replace_media_package_thumbnails,
)
from .inbox import SIZE, check_image, find_art, read_job

__all__ = ["import_owner_art"]

_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg"}


def import_owner_art(session: Session, store: ArtifactStore, package_id: str,
                     folder: Path, *, now: datetime) -> MediaPackage:
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
            checked.append((variant, result))
    if problems:
        raise MediaAssemblyError("thumbnail_art_invalid", "; ".join(problems))

    existing = {t.variant_key: t for t in list_thumbnail_variants(session, package_id)}
    offered = []
    for variant, result in checked:
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
    if not replace_media_package_thumbnails(
            session, package_id, offered, expected_status=PackageStatus.AWAITING_THUMBNAIL_ART,
            status=PackageStatus.READY_FOR_PUBLISH, now=now):
        raise MediaAssemblyError("thumbnail_art_not_awaited",
                                 f"{package_id} left {PackageStatus.AWAITING_THUMBNAIL_ART.value}")
    released = get_media_package_by_id(session, package_id)
    assert released is not None
    return released

"""The owner's changes to a finished package before it is approved for upload.

A package waiting for approval (``ready_for_publish``) can be changed in two ways that
need no new render, and one that does:

* :func:`edit_presentation`: a new title (of the first metadata candidate, the one P7
  publishes), description or tags (of every candidate), or a different thumbnail put
  first (P7 offers the first one). The description must keep every chapter timestamp,
  since the chapters are read from it. The translations keep their own text.
* :func:`reopen_art`: new art. The package goes back to ``awaiting_thumbnail_art``
  and forgets its imported art, so the owner's next import (which also renders the
  video again as the new image A, held still) replaces it.

Each refusal is a :class:`~media_factory.errors.MediaAssemblyError` with its code, and
nothing is written.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from .domain.enums import PackageStatus
from .domain.models import MediaPackage
from .errors import MediaAssemblyError
from .storage.repositories import (
    get_media_package_by_id,
    reopen_media_package_art,
    update_media_package_presentation,
)
from .thumbnail_art.guide import ART_VARIANTS

__all__ = ["MAX_DESCRIPTION", "MAX_TITLE", "edit_presentation", "reopen_art"]

MAX_TITLE = 100
MAX_DESCRIPTION = 5000
MAX_TAG_CHARACTERS = 500
"""YouTube's limits: title 100 characters, description 5,000, tags 500 in all."""


def _ready(session: Session, package_id: str) -> MediaPackage:
    pkg = get_media_package_by_id(session, package_id)
    if pkg is None:
        raise MediaAssemblyError("media_package_unknown", f"no media package {package_id!r}")
    if pkg.status is not PackageStatus.READY_FOR_PUBLISH:
        raise MediaAssemblyError(
            "package_not_in_review",
            f"{package_id} is {pkg.status.value}; only a package waiting for approval "
            f"({PackageStatus.READY_FOR_PUBLISH.value}) can be changed")
    return pkg


def _check_description(pkg: MediaPackage, description: str) -> None:
    if not description.strip() or len(description) > MAX_DESCRIPTION:
        raise MediaAssemblyError(
            "description_invalid",
            f"the description must have 1 to {MAX_DESCRIPTION} characters "
            f"(it has {len(description)})")
    if "<" in description or ">" in description:
        raise MediaAssemblyError("description_invalid",
                                 "YouTube refuses < and > in a description")
    missing = [c["timestamp_str"] for c in pkg.chapters
               if not re.search(rf"^{re.escape(str(c['timestamp_str']))}\b", description,
                                re.MULTILINE)]
    if missing:
        raise MediaAssemblyError(
            "chapters_missing",
            f"the description lost the chapter line(s) starting {', '.join(missing)}; "
            "each chapter needs its own line beginning with its timestamp")


def edit_presentation(session: Session, package_id: str, *, title: str | None = None,
                      description: str | None = None, tags: list[str] | None = None,
                      thumbnail_id: str | None = None, now: datetime) -> MediaPackage:
    """Apply the given edits (``None``: unchanged) and keep the package in review."""
    pkg = _ready(session, package_id)
    candidates = [dict(c) for c in pkg.metadata_candidates]
    if not candidates:
        raise MediaAssemblyError("metadata_missing", f"{package_id} has no metadata to edit")
    if title is not None:
        title = title.strip()
        if not title or len(title) > MAX_TITLE or "<" in title or ">" in title:
            raise MediaAssemblyError(
                "title_invalid", f"the title must have 1 to {MAX_TITLE} characters and no "
                f"< or > (it has {len(title)})")
        candidates[0]["title"] = title
    if description is not None:
        _check_description(pkg, description)
        for candidate in candidates:
            candidate["description"] = description
    if tags is not None:
        cleaned = [t.strip() for t in tags if t.strip()]
        if not cleaned or sum(len(t) for t in cleaned) > MAX_TAG_CHARACTERS:
            raise MediaAssemblyError(
                "tags_invalid", f"give 1 or more tags of at most {MAX_TAG_CHARACTERS} "
                "characters in all")
        for candidate in candidates:
            candidate["tags"] = cleaned
    thumbnails = [dict(t) for t in pkg.thumbnails]
    if thumbnail_id is not None:
        chosen = [t for t in thumbnails if t.get("id") == thumbnail_id]
        if not chosen:
            raise MediaAssemblyError("thumbnail_unknown",
                                     f"{package_id} has no thumbnail {thumbnail_id!r}")
        thumbnails = chosen + [t for t in thumbnails if t.get("id") != thumbnail_id]
    if not update_media_package_presentation(
            session, package_id, metadata_candidates=candidates, thumbnails=thumbnails,
            expected_status=PackageStatus.READY_FOR_PUBLISH, now=now):
        raise MediaAssemblyError("package_not_in_review", f"{package_id} left review")
    changed = get_media_package_by_id(session, package_id)
    assert changed is not None
    return changed


def reopen_art(session: Session, package_id: str, *, now: datetime) -> MediaPackage:
    """Send the package back to wait for new art (the run stays held for approval)."""
    _ready(session, package_id)
    if not reopen_media_package_art(
            session, package_id, variant_keys=ART_VARIANTS,
            expected_status=PackageStatus.READY_FOR_PUBLISH,
            status=PackageStatus.AWAITING_THUMBNAIL_ART, now=now):
        raise MediaAssemblyError("package_not_in_review", f"{package_id} left review")
    reopened = get_media_package_by_id(session, package_id)
    assert reopened is not None
    return reopened

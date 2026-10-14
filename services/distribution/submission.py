"""Submit a release from seam payloads: the one path the API, CLI and smoke test share.

Inputs are P5's per-track clearances (C5), P6's media package (C6 hop 1) and, optionally,
P7's publication event (C6 hop 2), all parsed with the shared ``plm_contracts`` models.
The release inherits the package's lineage (R9). A publication event, when given, must
belong to the same package and lineage. ISRCs come from the persisted per-recording
allocator (ADR-0029).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from plm_contracts import ErrorShape
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_contracts.seams.c6 import MediaPackagePayload, YouTubePublicationPayload
from sqlalchemy.orm import Session

from .artists.manager import get_or_create_artist
from .compiler.release_compiler import ReleaseClearanceError, compile_canonical_release_package
from .engine import DistributionEngine, DistributionEngineResult
from .identifiers.allocator import allocate_isrc

__all__ = ["ReleaseMetadata", "SubmissionRefusedError", "submit_release_from_seams"]


class SubmissionRefusedError(ValueError):
    """The inputs cannot become a release; ``error`` carries the reason code and fields."""

    def __init__(self, error: ErrorShape) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")


class ReleaseMetadata:
    """Release-level facts the caller decides (P8 §11); none is defaulted here."""

    def __init__(
        self,
        *,
        artist_name: str,
        release_date: str,
        label_name: str,
        copyright_line: str,
        publisher_line: str,
        territories: str,
        release_title: str | None = None,
        release_version: int = 1,
    ) -> None:
        self.artist_name = artist_name
        self.release_date = release_date
        self.label_name = label_name
        self.copyright_line = copyright_line
        self.publisher_line = publisher_line
        self.territories = territories
        self.release_title = release_title
        self.release_version = release_version


def _parse(model: Any, raw: Mapping[str, Any], prefix: str) -> Any:
    try:
        return parse_seam(model, raw)
    except SeamRejection as exc:
        raise SubmissionRefusedError(
            ErrorShape(
                code=exc.error.code,
                field_paths=[f"{prefix}.{p}" for p in exc.field_paths],
                message=exc.error.message,
            )
        ) from exc


def submit_release_from_seams(
    session: Session,
    engine: DistributionEngine,
    *,
    track_manifests: Sequence[Mapping[str, Any]],
    media_package: Mapping[str, Any],
    publication: Mapping[str, Any] | None,
    metadata: ReleaseMetadata,
    now: datetime,
) -> DistributionEngineResult:
    """Parse, compile and submit one release; refuse with a reason code on any gap."""
    manifests = [
        _parse(ReleaseManifestPayload, m, f"track_manifests[{i}]")
        for i, m in enumerate(track_manifests)
    ]
    package: MediaPackagePayload = _parse(MediaPackagePayload, media_package, "media_package")
    if publication is not None:
        event: YouTubePublicationPayload = _parse(
            YouTubePublicationPayload, publication, "publication"
        )
        if (
            event.media_package_id != package.media_package_id
            or event.lineage != package.lineage
        ):
            raise SubmissionRefusedError(
                ErrorShape(
                    code="publication_mismatch",
                    field_paths=["publication"],
                    message="the publication event belongs to a different package or lineage",
                )
            )

    artist = get_or_create_artist(session, metadata.artist_name, brand_id=package.brand_id)
    try:
        release = compile_canonical_release_package(
            track_manifests=manifests,
            media_package=package,
            artist_id=artist.id,
            artist_name=artist.canonical_name,
            release_title=metadata.release_title,
            release_version=metadata.release_version,
            release_date=metadata.release_date,
            label_name=metadata.label_name,
            copyright_line=metadata.copyright_line,
            publisher_line=metadata.publisher_line,
            territories=metadata.territories,
            allocate_isrc=lambda key: allocate_isrc(session, key, now=now),
            now=now,
        )
    except ReleaseClearanceError as exc:
        raise SubmissionRefusedError(exc.error) from exc

    return engine.submit_release(
        session,
        release,
        artist=artist,
        lineage=package.lineage,
        media_package_id=package.media_package_id,
    )

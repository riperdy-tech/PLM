"""Domain layer for Phase P8 DSP / Music Distribution Engine."""

from __future__ import annotations

from .enums import (
    ArtistType,
    DeliveryStatus,
    ISRCSource,
    ReleaseStatus,
    ReleaseType,
    StorePlatform,
)
from .ids import (
    build_release_id,
    build_submission_fingerprint,
    build_track_id,
    compute_sha256,
    generate_deterministic_isrc,
)
from .models import (
    ArtistProfile,
    CanonicalReleasePackage,
    DeliveryTarget,
    DistributionRecordPayload,
    DistributionRelease,
    DistributionReportReference,
    DistributionTrack,
)

__all__ = [
    "ArtistProfile",
    "ArtistType",
    "CanonicalReleasePackage",
    "DeliveryStatus",
    "DeliveryTarget",
    "DistributionRecordPayload",
    "DistributionRelease",
    "DistributionReportReference",
    "DistributionTrack",
    "ISRCSource",
    "ReleaseStatus",
    "ReleaseType",
    "StorePlatform",
    "build_release_id",
    "build_submission_fingerprint",
    "build_track_id",
    "compute_sha256",
    "generate_deterministic_isrc",
]

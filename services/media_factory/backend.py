"""What the P6 API assembles with: its authorities, store, toolchain and brands.

:func:`deployed_backend` builds the production set: P5 over HTTP
(``PLM_SERVICE_URL__QA``) and the platform exception queue for clearance, MinIO for
artifacts, FFmpeg for rendering, and the brand registry (``PLM_MEDIA_BRANDS_FILE`` or
the development default). Without a service token P5 cannot be asked, so there is no
backend and the assemble endpoint answers 503.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from plm_platform.clearance import HoldSource, HttpManifestSource, ManifestSource, QueueHoldSource
from plm_platform.config import Settings
from plm_platform.service_urls import service_url
from plm_platform.temporal import Phase
from sqlalchemy.orm import Session

from .artifacts import ArtifactStore, MinioArtifactStore
from .brands.registry import BRANDS_FILE_VAR, DEFAULT_BRANDS, BrandRegistry
from .rendering.ffmpeg import FFmpegToolchain
from .rendering.toolchain import MediaToolchain

__all__ = ["MediaBackend", "deployed_backend"]


@dataclass(frozen=True)
class MediaBackend:
    manifests: ManifestSource
    holds: HoldSource
    store: ArtifactStore
    toolchain: MediaToolchain
    brands: BrandRegistry


def deployed_backend(settings: Settings,
                     session_factory: Callable[[], Session]) -> MediaBackend | None:
    """The production backend, or ``None`` when no service token lets P6 ask P5."""
    if settings.service_token is None:
        return None
    brands_file = os.environ.get(BRANDS_FILE_VAR, "").strip()
    return MediaBackend(
        manifests=HttpManifestSource(service_url(Phase.QA),
                                     token=settings.service_token.get_secret_value()),
        holds=QueueHoldSource(session_factory),
        store=MinioArtifactStore(settings),
        toolchain=FFmpegToolchain(),
        brands=BrandRegistry.from_json_file(brands_file) if brands_file else DEFAULT_BRANDS,
    )

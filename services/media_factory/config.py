"""Media Factory settings and configuration knobs (P6 §8, §31, §39)."""

from __future__ import annotations

from dataclasses import dataclass

from plm_platform.config import Settings as PlatformSettings

__all__ = ["MediaFactorySettings", "load_media_factory_settings"]


@dataclass(frozen=True, slots=True)
class MediaFactorySettings:
    """Settings specific to the Playlist, Visual & Video Factory (Media Factory)."""

    platform: PlatformSettings | None = None
    host: str = "127.0.0.1"
    port: int = 8006
    default_render_profile: str = "yt_longform_1080p_v1"
    artifacts_bucket: str = "plm-artifacts"


def load_media_factory_settings(
    *, platform: PlatformSettings | None = None, **overrides: object
) -> MediaFactorySettings:
    """Build Media Factory settings."""
    unknown = set(overrides) - set(MediaFactorySettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown media factory settings: {sorted(unknown)}")
    return MediaFactorySettings(platform=platform, **overrides)  # type: ignore[arg-type]

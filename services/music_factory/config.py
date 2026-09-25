"""Music Factory settings and configuration knobs (§4.5, §111)."""

from __future__ import annotations

from dataclasses import dataclass

from plm_platform.config import Settings as PlatformSettings

__all__ = ["MusicFactorySettings", "load_music_factory_settings"]


@dataclass(frozen=True, slots=True)
class MusicFactorySettings:
    """Settings specific to the Music Generation Factory service."""

    platform: PlatformSettings | None = None
    default_provider: str = "mock"
    default_model: str = "mock-v1"
    host: str = "127.0.0.1"
    port: int = 8004
    artifacts_bucket: str = "plm-artifacts"
    max_parallel_generations: int = 4


def load_music_factory_settings(
    *, platform: PlatformSettings | None = None, **overrides: object
) -> MusicFactorySettings:
    """Build Music Factory settings."""
    unknown = set(overrides) - set(MusicFactorySettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown music factory settings: {sorted(unknown)}")
    return MusicFactorySettings(platform=platform, **overrides)  # type: ignore[arg-type]

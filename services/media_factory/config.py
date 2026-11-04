"""Media Factory settings and configuration knobs (P6 §8, §31, §39)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from plm_platform.config import Settings as PlatformSettings
from plm_platform.config import SettingsError

__all__ = ["RENDER_PROFILE_VAR", "MediaFactorySettings", "load_media_factory_settings"]

RENDER_PROFILE_VAR: Final[str] = "PLM_MEDIA_RENDER_PROFILE"
"""The deployment's render profile for a request that names none (P6's presentation
setting); unset: ``yt_longform_1080p_v1``."""


@dataclass(frozen=True, slots=True)
class MediaFactorySettings:
    """Settings specific to the Playlist, Visual & Video Factory (Media Factory)."""

    platform: PlatformSettings | None = None
    host: str = "127.0.0.1"
    port: int = 8006
    default_render_profile: str = "yt_longform_1080p_v1"
    """The profile an assemble request that names none is rendered with."""
    artifacts_bucket: str = "plm-artifacts"
    thumbnail_art_inbox: Path | None = None
    """``PLM_THUMBNAIL_ART_INBOX``: set, a rendered package waits for the owner's thumbnail
    art there (:mod:`.thumbnail_art.inbox`); unset, P6's drawn thumbnails are offered."""


def load_media_factory_settings(
    *, platform: PlatformSettings | None = None, env: Mapping[str, str] | None = None,
    **overrides: object,
) -> MediaFactorySettings:
    """Build Media Factory settings; ``PLM_MEDIA_RENDER_PROFILE`` (process environment)
    sets ``default_render_profile`` unless overridden. An undeclared profile fails at
    boot (:class:`~plm_platform.config.SettingsError`), not at the first request."""
    from .rendering.profiles import RENDER_PROFILES
    from .thumbnail_art.inbox import art_inbox

    unknown = set(overrides) - set(MediaFactorySettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown media factory settings: {sorted(unknown)}")
    source = os.environ if env is None else env
    configured = source.get(RENDER_PROFILE_VAR, "").strip()
    if configured and "default_render_profile" not in overrides:
        overrides["default_render_profile"] = configured
    if "thumbnail_art_inbox" not in overrides:
        overrides["thumbnail_art_inbox"] = art_inbox(source)
    settings = MediaFactorySettings(platform=platform, **overrides)  # type: ignore[arg-type]
    if settings.default_render_profile not in RENDER_PROFILES:
        raise SettingsError(
            f"{RENDER_PROFILE_VAR}: render profile {settings.default_render_profile!r} is "
            f"not declared; known: {sorted(RENDER_PROFILES)}")
    return settings

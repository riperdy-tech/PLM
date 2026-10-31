"""Music Factory settings and configuration knobs (§4.5, §111)."""

from __future__ import annotations

from dataclasses import dataclass

from plm_contracts import ProviderMode
from plm_platform.config import Settings as PlatformSettings
from plm_platform.execution import ExecutionSettings

__all__ = ["MusicFactorySettings", "default_provider_for", "load_music_factory_settings"]


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


def default_provider_for(execution: ExecutionSettings, *, manual_inbox: bool = False) -> str:
    """``PLM_PROVIDER_MODE__MUSIC=real`` selects Suno; anything else keeps the mock.

    With a manual inbox (``PLM_SUNO_MANUAL_INBOX``) real Suno means the owner's own
    downloads (``suno_manual``), never the automated client. Selecting either does not let
    it run: each call still needs a LIVE policy, an owner grant and a funded budget
    ceiling (``ExternalWriteGuard``).
    """
    if execution.provider_mode("music") is not ProviderMode.REAL:
        return "mock"
    return "suno_manual" if manual_inbox else "suno"

"""Quality Gate settings and configuration knobs (P5 §4.5, §94)."""

from __future__ import annotations

from dataclasses import dataclass

from plm_platform.config import Settings as PlatformSettings
from plm_platform.exceptions import NO_REVIEWERS, ReviewerDirectory

__all__ = ["QualityGateSettings", "load_quality_gate_settings"]


@dataclass(frozen=True, slots=True)
class QualityGateSettings:
    """Settings specific to the Quality, Rights & Release Gate service."""

    platform: PlatformSettings | None = None
    host: str = "127.0.0.1"
    port: int = 8005
    default_policy_version: str = "p5-v1"
    artifacts_bucket: str = "plm-artifacts"
    reviewers: ReviewerDirectory = NO_REVIEWERS
    """Who holds which reviewer role (R3 exception queue). The default grants nobody a
    role, so no quarantine can be overridden until a directory is injected."""


def load_quality_gate_settings(
    *, platform: PlatformSettings | None = None, **overrides: object
) -> QualityGateSettings:
    """Build Quality Gate settings."""
    unknown = set(overrides) - set(QualityGateSettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown quality gate settings: {sorted(unknown)}")
    return QualityGateSettings(platform=platform, **overrides)  # type: ignore[arg-type]

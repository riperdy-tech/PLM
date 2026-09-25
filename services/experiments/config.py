"""P3 settings: deployment values come from `plm_platform.config`; these are P3's knobs.

Constructed from code defaults plus explicit overrides — P3 never reads `os.environ`.
Version strings live here once and are copied onto experiments and packages.
"""

from __future__ import annotations

from dataclasses import dataclass

from plm_platform.config import Settings

__all__ = ["ExperimentsSettings", "load_experiments_settings"]


@dataclass(frozen=True, slots=True)
class ExperimentsSettings:
    platform: Settings | None = None
    engine_version: str = "p3-v1"
    genome_version: str = "p3-genome-v1"
    experiment_version: str = "p3-exp-v1"
    materializer_version: str = "p3-mat-v1"


def load_experiments_settings(
    *, platform: Settings | None = None, **overrides: object
) -> ExperimentsSettings:
    """Build P3 settings; unknown keyword overrides raise (a typo must not pass silently)."""
    unknown = set(overrides) - set(ExperimentsSettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown experiments settings: {sorted(unknown)}")
    return ExperimentsSettings(platform=platform, **overrides)  # type: ignore[arg-type]

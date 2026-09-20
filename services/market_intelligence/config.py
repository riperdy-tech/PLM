# services/market_intelligence/config.py
"""P1 settings.

`plm_platform.config.Settings` owns deployment values and secrets; this dataclass adds
P1's operational knobs (versions, budgets, cadences, weights, thresholds). It is
constructed from code defaults plus explicit overrides — P1 never reads `os.environ`
itself, and no algorithm contains a literal threshold (§9, §26, §43).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from plm_platform.config import Settings

__all__ = ["MarketSettings", "load_market_settings"]


@dataclass(frozen=True, slots=True)
class CadenceHours:
    """§9's suggested initial schedule; every value is configuration, not a literal."""

    high_priority: float = 24.0
    normal: float = 72.0
    long_tail: float = 168.0


@dataclass(frozen=True, slots=True)
class MarketSettings:
    platform: Settings | None
    score_version: str = "p1-v1"
    taxonomy_version: str = "p1-taxonomy-v1"
    feature_version: str = "p1-features-v1"
    normalization_version: str = "p1-norm-v1"
    default_brand_id: str = "neon_seoul"
    cadence: CadenceHours = field(default_factory=CadenceHours)
    quota_units_per_run: int = 1000
    quota_units_per_day: int = 10_000
    max_requests_per_run: int = 200
    requests_per_second: float = 5.0
    max_pages_per_search: int = 2
    max_results_per_search: int = 10
    refresh_high_velocity_hours: float = 24.0
    refresh_normal_hours: float = 72.0
    freshness_fresh_hours: float = 48.0
    freshness_normal_hours: float = 168.0
    freshness_stale_hours: float = 720.0
    min_arm_support: int = 2
    schema_failure_partial_threshold: float = 0.05
    change_score_threshold: float = 10.0
    change_growth_threshold: float = 15.0
    confidence_sample_target: int = 25
    confidence_source_target: int = 2
    llm_cost_estimate_per_1k_tokens: float = 0.0


def load_market_settings(
    *, platform: Settings | None = None, **overrides: object
) -> MarketSettings:
    """Build P1 settings; `platform=None` defers platform settings to the caller.

    Tests pass an explicit `platform=Settings(...)` (or `platform=None` in unit tests
    that never touch the database) and override individual knobs by keyword.
    """
    unknown = set(overrides) - {f for f in MarketSettings.__dataclass_fields__}
    if unknown:
        raise TypeError(f"unknown market settings: {sorted(unknown)}")
    resolved_platform = platform if platform is not None else None
    return MarketSettings(platform=resolved_platform, **overrides)  # type: ignore[arg-type]

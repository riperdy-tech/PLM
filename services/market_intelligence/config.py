# services/market_intelligence/config.py
"""P1 settings.

`plm_platform.config.Settings` owns deployment values and secrets; this dataclass adds
P1's operational knobs (versions, budgets, cadences, weights, thresholds). It is
constructed from code defaults plus explicit overrides — P1 never reads `os.environ`
itself, and no algorithm contains a literal threshold (§9, §26, §43).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from plm_platform.config import Settings

from .scoring.calibration import ScoreWeights, default_weights

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
    taxonomy_version: str = "p1-taxonomy-v3"
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
    # ADR-0044: English-first audience; search.list is biased to it. `None` omits the
    # parameter, so an operator can widen the market without a code change.
    search_region_code: str | None = "US"
    search_relevance_language: str | None = "en"
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
    # Review ruling: an arm whose measured basis is below this share of the total
    # weight is returned but not ranked in the opportunities list (insufficient
    # coverage), so a two-component guess never masquerades as a full-basis score.
    min_rank_basis_weight: float = 0.40
    llm_cost_estimate_per_1k_tokens: float = 0.0
    # §17.2: "All weights must be configuration values." Validated on construction
    # (finite, non-negative, summing to 1): a bad weight set refuses to load rather
    # than producing scores. Changing them is a score-version change, not a tweak.
    score_weights: ScoreWeights = field(default_factory=default_weights)
    # Short-horizon velocity (§9's 24h/72h refresh cadences): two sightings inside the
    # window must span at least this share of it, or the velocity is unknown — two
    # sightings a minute apart are not a 72-hour rate.
    velocity_min_span_fraction: float = 0.5

    def __post_init__(self) -> None:
        self.score_weights.validate()
        if not 0.0 < self.velocity_min_span_fraction <= 1.0:
            raise ValueError(
                "velocity_min_span_fraction must be in (0, 1], got "
                f"{self.velocity_min_span_fraction!r}"
            )


def load_market_settings(
    *, platform: Settings | None = None, **overrides: object
) -> MarketSettings:
    """Build P1 settings; `platform=None` defers platform settings to the caller.

    Tests pass an explicit `platform=Settings(...)` (or `platform=None` in unit tests
    that never touch the database) and override individual knobs by keyword.
    `score_weights` may be a `ScoreWeights` or a mapping naming every component; either
    way it is validated, and an invalid set raises instead of loading.
    """
    unknown = set(overrides) - {f for f in MarketSettings.__dataclass_fields__}
    if unknown:
        raise TypeError(f"unknown market settings: {sorted(unknown)}")
    weights = overrides.get("score_weights")
    if isinstance(weights, Mapping):
        overrides["score_weights"] = ScoreWeights.from_mapping(weights)
    elif weights is not None and not isinstance(weights, ScoreWeights):
        raise TypeError("score_weights must be a ScoreWeights or a mapping of component weights")
    resolved_platform = platform if platform is not None else None
    return MarketSettings(platform=resolved_platform, **overrides)  # type: ignore[arg-type]

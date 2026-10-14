# services/market_intelligence/scoring/calibration.py
"""§17.2: all weights are configuration values, validated to sum to 1.0."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Final, Self

__all__ = ["SCORE_VERSION", "WEIGHT_SUM_TOLERANCE", "ScoreWeights", "default_weights"]

SCORE_VERSION: Final[str] = "p1-v1"

WEIGHT_SUM_TOLERANCE: Final[float] = 1e-9


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    demand: float
    growth: float
    competitor_velocity: float
    competition: float
    audience_quality: float
    cross_platform: float
    production_fit: float

    @property
    def total(self) -> float:
        return sum(self.as_mapping().values())

    def as_mapping(self) -> dict[str, float]:
        return {"demand": self.demand, "growth": self.growth,
                "competitor_velocity": self.competitor_velocity,
                "competition": self.competition, "audience_quality": self.audience_quality,
                "cross_platform": self.cross_platform, "production_fit": self.production_fit}

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> Self:
        """Build from configuration; every component must be named, nothing extra.

        A missing component is refused rather than defaulted: an unstated weight is
        not a zero weight, and a typo must not silently drop a component.
        """
        expected = {field.name for field in fields(cls)}
        missing = sorted(expected - set(values))
        unknown = sorted(set(values) - expected)
        if missing or unknown:
            raise ValueError(
                f"score weights must name exactly {sorted(expected)}; "
                f"missing {missing}, unknown {unknown}"
            )
        converted: dict[str, float] = {}
        for name in sorted(expected):
            raw = values[name]
            if isinstance(raw, bool) or not isinstance(raw, int | float | str):
                raise TypeError(f"weight {name} must be a number, got {type(raw).__name__}")
            converted[name] = float(raw)
        return cls(**converted).validate()

    def validate(self) -> Self:
        for name, weight in self.as_mapping().items():
            if not math.isfinite(weight):
                raise ValueError(f"weight {name} must be finite, got {weight}")
            if weight < 0:
                raise ValueError(f"weight {name} cannot be negative, got {weight}")
        if abs(self.total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"score weights must sum to 1.0, got {self.total}")
        return self


def default_weights() -> ScoreWeights:
    """§17.2's initial weights; a change here is a score-version change, not a tweak."""
    return ScoreWeights(demand=0.25, growth=0.20, competitor_velocity=0.15, competition=0.15,
                        audience_quality=0.10, cross_platform=0.10, production_fit=0.05)

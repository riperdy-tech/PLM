# services/market_intelligence/scoring/calibration.py
"""§17.2: all weights are configuration values, validated to sum to 1.0."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Self

__all__ = ["SCORE_VERSION", "ScoreWeights", "default_weights"]

SCORE_VERSION: Final[str] = "p1-v1"

_SUM_TOLERANCE: Final[float] = 1e-9


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

    def validate(self) -> Self:
        for name, weight in self.as_mapping().items():
            if weight < 0:
                raise ValueError(f"weight {name} cannot be negative, got {weight}")
        if abs(self.total - 1.0) > _SUM_TOLERANCE:
            raise ValueError(f"score weights must sum to 1.0, got {self.total}")
        return self


def default_weights() -> ScoreWeights:
    """§17.2's initial weights; a change here is a score-version change, not a tweak."""
    return ScoreWeights(demand=0.25, growth=0.20, competitor_velocity=0.15, competition=0.15,
                        audience_quality=0.10, cross_platform=0.10, production_fit=0.05)

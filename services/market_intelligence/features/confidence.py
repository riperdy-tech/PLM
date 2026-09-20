# services/market_intelligence/features/confidence.py
"""§19: confidence is independent of the opportunity score and clamped to 0–1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = ["DEFAULT_CONFIDENCE_WEIGHTS", "ConfidenceBreakdown", "confidence"]

DEFAULT_CONFIDENCE_WEIGHTS: Final[Mapping[str, float]] = {
    "sample": 0.35, "source_diversity": 0.20, "time_series_depth": 0.15,
    "taxonomy": 0.15, "freshness": 0.15,
}


@dataclass(frozen=True, slots=True)
class ConfidenceBreakdown:
    """Each component 0–1; the combination is configuration, not a magic number."""

    sample: float
    source_diversity: float
    time_series_depth: float
    taxonomy: float
    freshness: float

    def __post_init__(self) -> None:
        for name, value in self.as_mapping().items():
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"confidence component {name} must be within 0–1")

    def as_mapping(self) -> dict[str, float]:
        return {"sample": self.sample, "source_diversity": self.source_diversity,
                "time_series_depth": self.time_series_depth, "taxonomy": self.taxonomy,
                "freshness": self.freshness}


def confidence(
    breakdown: ConfidenceBreakdown, *, weights: Mapping[str, float] | None = None,
) -> float:
    """Weighted combination, clamped. §17.2: this never multiplies the score."""
    active = DEFAULT_CONFIDENCE_WEIGHTS if weights is None else weights
    total_weight = sum(active.values())
    if total_weight <= 0:
        raise ValueError("confidence weights must sum above zero")
    value = sum(breakdown.as_mapping()[name] * weight for name, weight in active.items())
    return max(0.0, min(1.0, value / total_weight))

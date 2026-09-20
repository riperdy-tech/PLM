# services/market_intelligence/features/production_fit.py
"""§17.3 production fit: feasibility for *our* factory, explicitly not an economic
prediction. Everything is configuration-driven."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ..config import MarketSettings
from ..domain.value_objects import TaxonomyProfile

__all__ = ["ProductionFitInputs", "production_fit_inputs"]

# Vocal-mode and language vocabularies share this map; `mixed` occurs in both roles
# with the same ease (the plan's dict literal carried the key twice — F601).
_EASE: Final[Mapping[str, float]] = {
    "instrumental": 1.0, "vocal": 0.7, "mixed": 0.6, "not_applicable": 1.0,
    "english": 1.0, "korean": 0.8, "japanese": 0.8, "spanish": 0.9,
}


@dataclass(frozen=True, slots=True)
class ProductionFitInputs:
    vocal_compatibility: float
    language_support: float
    visual_feasibility: float
    copyright_risk_ease: float


def production_fit_inputs(profile: TaxonomyProfile, settings: MarketSettings) -> ProductionFitInputs:
    """Deterministic, rule-based, versioned by `settings.feature_version`."""
    keyword_risk = {None: 0.8, "city_pop": 0.6, "jazz": 0.7, "classical": 0.9}
    return ProductionFitInputs(
        vocal_compatibility=_EASE.get(profile.vocal_mode or "", 0.5),
        language_support=_EASE.get(profile.language or "", 0.5),
        visual_feasibility=0.9 if profile.visual_world else 0.5,
        copyright_risk_ease=keyword_risk.get(profile.genre, 0.5),
    )

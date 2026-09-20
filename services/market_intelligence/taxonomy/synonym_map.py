# services/market_intelligence/taxonomy/synonym_map.py
"""§14.1/§22: the phrase → canonical-value map that keeps `night drive` and
`late night driving` one use case while the original phrase survives on the row."""

from __future__ import annotations

from typing import Final

from ..domain.enums import TaxonomyDimension as D
from ..normalization.canonical import normalize_text

__all__ = ["SYNONYMS", "lookup_synonym"]

SYNONYMS: Final[dict[str, tuple[D, str]]] = {
    "rnb": (D.GENRE, "rnb"),
    "r&b": (D.GENRE, "rnb"),
    "rhythm and blues": (D.GENRE, "rnb"),
    "alt rnb": (D.SUBGENRE, "alt_rnb"),
    "lo-fi": (D.GENRE, "lofi"),
    "lofi": (D.GENRE, "lofi"),
    "synthwave": (D.GENRE, "synthwave"),
    "city pop": (D.GENRE, "city_pop"),
    "ambient": (D.GENRE, "ambient"),
    "deep house": (D.GENRE, "deep_house"),
    "melancholic": (D.MOOD, "melancholic"),
    "sad": (D.MOOD, "melancholic"),
    "dreamy": (D.MOOD, "dreamy"),
    "romantic": (D.MOOD, "romantic"),
    "late night drive": (D.USE_CASE, "late_night_drive"),
    "late night driving": (D.USE_CASE, "late_night_drive"),
    "late night": (D.USE_CASE, "late_night_drive"),
    "night drive": (D.USE_CASE, "late_night_drive"),
    "2am drive": (D.USE_CASE, "late_night_drive"),
    "2am": (D.USE_CASE, "late_night_drive"),
    "midnight drive": (D.USE_CASE, "late_night_drive"),
    "study": (D.USE_CASE, "study_focus"),
    "focus": (D.USE_CASE, "study_focus"),
    "sleep": (D.USE_CASE, "sleep"),
    "cafe": (D.USE_CASE, "cafe_background"),
    "english": (D.LANGUAGE, "english"),
    "korean": (D.LANGUAGE, "korean"),
    "vocal": (D.VOCAL_MODE, "vocal"),
    "instrumental": (D.VOCAL_MODE, "instrumental"),
    "female vocal": (D.VOCAL_GENDER, "female"),
    "male vocal": (D.VOCAL_GENDER, "male"),
    "low energy": (D.ENERGY, "low"),
    "high energy": (D.ENERGY, "high"),
    "chill": (D.ENERGY, "low_medium"),
    "rainy": (D.VISUAL_WORLD, "rainy_city"),
    "rainy asian city": (D.VISUAL_WORLD, "rainy_asian_city"),
    "seoul": (D.VISUAL_WORLD, "rainy_asian_city"),
    "tokyo": (D.VISUAL_WORLD, "neon_tokyo"),
    "neon": (D.VISUAL_WORLD, "neon_tokyo"),
    "lounge": (D.AUDIENCE_INTENT, "background_listening"),
    "background": (D.AUDIENCE_INTENT, "background_listening"),
    "time place mood": (D.TITLE_ARCHETYPE, "time_place_mood"),
    "45 75m": (D.DURATION_BUCKET, "45_75m"),
    "45_75m": (D.DURATION_BUCKET, "45_75m"),
    "60_180s": (D.DURATION_BUCKET, "60_180s"),
    "82_96": (D.BPM_RANGE, "82_96"),
}


def lookup_synonym(term: str) -> tuple[D, str] | None:
    """Normalised lookup; unknown phrases return `None` (the `other_candidate` path)."""
    return SYNONYMS.get(normalize_text(term))

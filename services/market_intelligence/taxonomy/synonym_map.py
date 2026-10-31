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
    # ADR-0044 focus-electronic space. A sub-style phrase names the subgenre; the
    # classifier fills the genre from it. Bare "electronic" is left unmapped on
    # purpose: it covers EDM and festival music, which the identity excludes.
    "focus electronic": (D.GENRE, "focus_electronic"),
    "focus electronica": (D.GENRE, "focus_electronic"),
    "electronic focus": (D.GENRE, "focus_electronic"),
    "ambient electronic": (D.GENRE, "focus_electronic"),
    "ambient techno": (D.GENRE, "focus_electronic"),
    "electronica": (D.GENRE, "focus_electronic"),
    "future garage": (D.SUBGENRE, "future_garage_focus"),
    "atmospheric electronica": (D.SUBGENRE, "soft_cyber_electronica"),
    "soft electronica": (D.SUBGENRE, "soft_cyber_electronica"),
    "cyber electronica": (D.SUBGENRE, "soft_cyber_electronica"),
    "minimal electronic": (D.SUBGENRE, "minimal_pulse"),
    "minimal techno": (D.SUBGENRE, "minimal_pulse"),
    "breakbeat": (D.SUBGENRE, "ambient_breakbeat"),
    "downtempo": (D.SUBGENRE, "downtempo_synth"),
    "chillstep": (D.SUBGENRE, "deep_work_chillstep"),
    "retro future": (D.SUBGENRE, "restrained_retro_future"),
    "retro-future": (D.SUBGENRE, "restrained_retro_future"),
    "retrofuture": (D.SUBGENRE, "restrained_retro_future"),
    "retro futuristic": (D.SUBGENRE, "restrained_retro_future"),
    "organic electronic": (D.SUBGENRE, "organic_electronic"),
    "organic electronica": (D.SUBGENRE, "organic_electronic"),
    "piano synth": (D.SUBGENRE, "piano_synth_concentration"),
    "piano and synth": (D.SUBGENRE, "piano_synth_concentration"),
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
    "studying": (D.USE_CASE, "study_focus"),
    "focus": (D.USE_CASE, "study_focus"),
    "focused": (D.USE_CASE, "study_focus"),
    # Coding and deep work are `study_focus`: it is the focus use case P2's brand
    # identity admits (services/autonomous_ar/policy/brand_identity.json).
    "deep focus": (D.USE_CASE, "study_focus"),
    "deep work": (D.USE_CASE, "study_focus"),
    "deep-work": (D.USE_CASE, "study_focus"),
    "concentration": (D.USE_CASE, "study_focus"),
    "coding": (D.USE_CASE, "study_focus"),
    "programming": (D.USE_CASE, "study_focus"),
    "sleep": (D.USE_CASE, "sleep"),
    "sleeping": (D.USE_CASE, "sleep"),
    "cafe": (D.USE_CASE, "cafe_background"),
    "english": (D.LANGUAGE, "english"),
    "korean": (D.LANGUAGE, "korean"),
    "vocal": (D.VOCAL_MODE, "vocal"),
    "instrumental": (D.VOCAL_MODE, "instrumental"),
    # Negations outrank "vocal" because the classifier keeps the longest phrase
    # covering the same words; a plural `s` is matched by the classifier.
    "no vocal": (D.VOCAL_MODE, "instrumental"),
    "without vocal": (D.VOCAL_MODE, "instrumental"),
    "non vocal": (D.VOCAL_MODE, "instrumental"),
    "non-vocal": (D.VOCAL_MODE, "instrumental"),
    "vocal free": (D.VOCAL_MODE, "instrumental"),
    "vocal-free": (D.VOCAL_MODE, "instrumental"),
    "no lyric": (D.VOCAL_MODE, "instrumental"),
    "without lyric": (D.VOCAL_MODE, "instrumental"),
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

# services/market_intelligence/taxonomy/ontology.py
"""§14: the controlled vocabulary. R14 fixes the dimension set at thirteen.

Values are tokens, never free text; `other_candidate` is the only escape hatch and it
is explicit (§14.3). Unknown terms are *recorded* as unmapped, not mapped silently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from ..domain.enums import TaxonomyDimension as D
from ..domain.value_objects import TaxonomyProfile
from ..normalization.canonical import normalize_text
from .synonym_map import lookup_synonym

__all__ = [
    "DIMENSION_SPECS", "OTHER_CANDIDATE", "DimensionSpec", "controlled_values",
    "is_controlled_value", "normalize_dimension_value", "profile_from_dimensions",
]

OTHER_CANDIDATE: Final[str] = "other_candidate"


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    dimension: D
    values: frozenset[str]
    multi: bool = False
    allow_other_candidate: bool = True


def _spec(dimension: D, *values: str, multi: bool = False) -> DimensionSpec:
    return DimensionSpec(dimension=dimension, values=frozenset({*values, OTHER_CANDIDATE}),
                         multi=multi)


DIMENSION_SPECS: Final[Mapping[D, DimensionSpec]] = {
    D.GENRE: _spec(D.GENRE, "rnb", "pop", "indie", "city_pop", "synthwave", "lofi", "ambient",
                   "house", "deep_house", "jazz", "acoustic", "classical", "hip_hop", "rock"),
    D.SUBGENRE: _spec(D.SUBGENRE, "alt_rnb", "bedroom_pop", "dream_pop", "neo_soul",
                      "lofi_hip_hop", "chillhop", "deep_house", "progressive_house"),
    D.MOOD: _spec(D.MOOD, "melancholic", "dreamy", "romantic", "calm", "nostalgic", "hopeful",
                  "dark", "uplifting", "sensual", "lonely", multi=True),
    D.USE_CASE: _spec(D.USE_CASE, "late_night_drive", "study_focus", "sleep", "cafe_background",
                      "workout", "party", "morning_routine", "rainy_day", "work_focus",
                      multi=True),
    D.LANGUAGE: _spec(D.LANGUAGE, "english", "korean", "japanese", "spanish", "instrumental",
                      "mixed"),
    D.VOCAL_MODE: _spec(D.VOCAL_MODE, "vocal", "instrumental", "mixed"),
    D.VOCAL_GENDER: _spec(D.VOCAL_GENDER, "female", "male", "mixed", "not_applicable"),
    D.ENERGY: _spec(D.ENERGY, "low", "low_medium", "medium", "medium_high", "high"),
    D.BPM_RANGE: _spec(D.BPM_RANGE, "lt_70", "70_85", "82_96", "85_100", "100_120", "120_140",
                       "gt_140"),
    D.DURATION_BUCKET: _spec(D.DURATION_BUCKET, "lt_60s", "60_180s", "3_10m", "10_45m",
                             "45_75m", "gt_75m"),
    D.VISUAL_WORLD: _spec(D.VISUAL_WORLD, "rainy_city", "rainy_asian_city", "neon_tokyo",
                          "night_highway", "cozy_room", "minimal_abstract", multi=True),
    D.AUDIENCE_INTENT: _spec(D.AUDIENCE_INTENT, "background_listening", "active_listening",
                             "mood_regulation", "focus_support", "social_ambience"),
    D.TITLE_ARCHETYPE: _spec(D.TITLE_ARCHETYPE, "time_place_mood", "genre_plus_use_case",
                             "question_how_to", "number_list", "artist_adjacent",
                             multi=True),
}
"""The 13 §14.2 dimensions with a starter vocabulary; expansion is an explicit
workflow (§14.3), so `values` may grow but never silently shrink."""


def controlled_values(dimension: D) -> frozenset[str]:
    return DIMENSION_SPECS[dimension].values


def is_controlled_value(dimension: D, value: str) -> bool:
    return normalize_text(value).replace(" ", "_") in DIMENSION_SPECS[dimension].values


def normalize_dimension_value(dimension: D, value: str) -> str | None:
    """A raw phrase → its controlled token, or `None` (never the nearest label)."""
    token = normalize_text(value).replace(" ", "_")
    if token in DIMENSION_SPECS[dimension].values:
        return token
    found = lookup_synonym(value)
    if found is not None and found[0] is dimension:
        return found[1]
    return None


def profile_from_dimensions(dimensions: Mapping[D, Sequence[str]]) -> TaxonomyProfile:
    """Build the identity object; multi-valued dimensions keep the ontology's order."""
    single: dict[str, str | None] = {dimension.value: None for dimension in D}
    moods: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    visuals: tuple[str, ...] = ()
    for dimension, raw_values in dimensions.items():
        ordered = tuple(dict.fromkeys(raw_values))
        if dimension is D.MOOD:
            moods = ordered
        elif dimension is D.USE_CASE:
            use_cases = ordered
        elif dimension is D.VISUAL_WORLD:
            visuals = ordered
        else:
            single[dimension.value] = ordered[0] if ordered else None
    return TaxonomyProfile(
        genre=single[D.GENRE.value], subgenre=single[D.SUBGENRE.value], mood=moods,
        use_case=single[D.USE_CASE.value] or (use_cases[0] if use_cases else None),
        language=single[D.LANGUAGE.value], vocal_mode=single[D.VOCAL_MODE.value],
        vocal_gender=single[D.VOCAL_GENDER.value], energy=single[D.ENERGY.value],
        bpm_range=single[D.BPM_RANGE.value], duration_bucket=single[D.DURATION_BUCKET.value],
        visual_world=single[D.VISUAL_WORLD.value] or (visuals[0] if visuals else None),
        audience_intent=single[D.AUDIENCE_INTENT.value],
        title_archetype=single[D.TITLE_ARCHETYPE.value],
    )
